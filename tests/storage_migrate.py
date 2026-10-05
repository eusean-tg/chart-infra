#!/usr/bin/env python3
"""Offline migration safety, archive comparison and rollback checks."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'incus'))
import storage_migrate as m


class Migration(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'incus/host.example.json').read_text())
        self.c.update(machine_id='a'*32,hdd_uuid='14ef1cfa-28ee-4986-8989-2e248896bf07')

    def archive(self,path,*,payload=b'data',uid=0,mode=0o640,xattr='x',reverse=False):
        entries=['etc/machine-id']+['file'+str(i) for i in range(101)]
        if reverse: entries.reverse()
        with tarfile.open(path,'w') as archive:
            for name in entries:
                info=tarfile.TarInfo('backup/container/rootfs/'+name)
                info.size=len(payload); info.uid=uid; info.mode=mode
                info.pax_headers={'SCHILY.xattr.user.test':xattr}
                archive.addfile(info,io.BytesIO(payload))

    def test_manifest_ignores_tar_order_but_detects_content_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            first,second=Path(d)/'a.tar',Path(d)/'b.tar'
            self.archive(first); expected=m.rootfs_manifest(first)
            self.archive(second,reverse=True); self.assertEqual(expected,m.rootfs_manifest(second))
            for change in ({'payload':b'other'},{'uid':1000},{'mode':0o600},{'xattr':'y'}):
                self.archive(second,**change)
                self.assertNotEqual(expected['sha256'],m.rootfs_manifest(second)['sha256'])

    def test_incomplete_export_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'empty.tar'
            with tarfile.open(file,'w'): pass
            with self.assertRaisesRegex(RuntimeError,'complete'): m.rootfs_manifest(file)

    def test_compressed_hardlinks_use_cached_hashes_without_seeking(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'linked.tar.gz'; prefix='backup/container/rootfs/'
            payload=b'x'*65536
            with tarfile.open(path,'w:gz') as archive:
                first=tarfile.TarInfo(prefix+'etc/machine-id'); first.size=len(payload)
                archive.addfile(first,io.BytesIO(payload))
                for i in range(110):
                    entry=tarfile.TarInfo(prefix+'link'+str(i)); entry.type=tarfile.LNKTYPE
                    entry.linkname=prefix+('etc/machine-id' if i==0 else 'link'+str(i-1))
                    archive.addfile(entry)
            extract=tarfile.TarFile.extractfile; regular=[]
            def checked(archive,entry):
                self.assertTrue(entry.isfile(),'Hard links must not call extractfile')
                regular.append(entry.name); return extract(archive,entry)
            with patch.object(tarfile.TarFile,'extractfile',checked): result=m.rootfs_manifest(path)
            self.assertEqual(result['entries'],111); self.assertEqual(regular,[prefix+'etc/machine-id'])

    def test_forward_or_external_hardlinks_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            for target in ('backup/container/rootfs/later','backup/index.yaml','../../etc/passwd'):
                path=Path(d)/'bad.tar.gz'
                with tarfile.open(path,'w:gz') as archive:
                    entry=tarfile.TarInfo('backup/container/rootfs/link'); entry.type=tarfile.LNKTYPE
                    entry.linkname=target; archive.addfile(entry)
                with self.subTest(target=target),self.assertRaisesRegex(RuntimeError,'hard link'):
                    m.rootfs_manifest(path)

    def test_replace_preserves_mode_and_refuses_changed_or_symlinked_files(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'file'; file.write_bytes(b'old'); file.chmod(0o600)
            m.replace(file,b'new',b'old'); self.assertEqual(file.read_bytes(),b'new')
            self.assertEqual(file.stat().st_mode & 0o777,0o600)
            with self.assertRaises(RuntimeError): m.replace(file,b'bad',b'old')
            link=Path(d)/'link'; link.symlink_to(file)
            with self.assertRaises(RuntimeError): m.replace(link,b'bad')
            self.assertEqual(file.read_bytes(),b'new')

    def test_both_config_copies_preflight_before_either_write(self):
        with tempfile.TemporaryDirectory() as d:
            source,installed=Path(d)/'source',Path(d)/'installed'
            source.write_text(json.dumps(self.c)); installed.write_text('{}')
            with patch.object(m.p,'HOST_CONFIG',installed):
                with self.assertRaises(RuntimeError): m.set_config(source,self.c,{'new':True})
            self.assertEqual(json.loads(source.read_text()),self.c)

    def test_config_mapping_written_to_both_copies(self):
        with tempfile.TemporaryDirectory() as d:
            source,installed=Path(d)/'source',Path(d)/'installed'
            for file in (source,installed): file.write_text(json.dumps(self.c))
            new={**self.c,'instance_pools':{'dev':'hdd'}}
            with patch.object(m.p,'HOST_CONFIG',installed): m.set_config(source,self.c,new)
            self.assertEqual(json.loads(source.read_text()),new)
            self.assertEqual(json.loads(installed.read_text()),new)

    def test_resumed_proof_requires_original_hash_and_full_roundtrip(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m.p,'STATE',Path(d)):
            directory=Path(d)/'storage-moves/trial1'; directory.mkdir(parents=True)
            original=directory/'verify.json'; original.write_text('{"phase":"failed"}')
            proof={'owner':m.m.OWNER,'host':self.c['machine_id'],'trial':'trial1','phase':'verified','image':'f'*64,
                   'original_receipt_sha256':m.p.digest(original),
                   'moves':[{'pool':pool,'files_and_uid_map':'passed','docker':'passed'} for pool in ('hdd','ssd','hdd')]}
            resumed=directory/'resume.json'; resumed.write_text(json.dumps(proof))
            self.assertEqual(m.verified_trial(self.c,'trial1','f'*64)['path'],str(resumed))
            for changed in ({'phase':'failed'},{'moves':proof['moves'][:2]},{'image':'e'*64},
                            {'original_receipt_sha256':'wrong'}):
                resumed.write_text(json.dumps({**proof,**changed}))
                with self.assertRaises(RuntimeError): m.verified_trial(self.c,'trial1','f'*64)

    def test_identity_mismatch_is_not_accepted(self):
        with patch.object(m.m,'guest',return_value='{"id":"changed"}'):
            with self.assertRaisesRegex(RuntimeError,'identity'): m.wait_identity(self.c,'dev',{'id':'original'})

    def test_source_capacity_reserves_rollback_room(self):
        report={'review':False,'pool':{'total':1000,'used':500}}
        with patch.object(m.p,'capacity',return_value=report),patch.object(m.backup,'storage') as storage:
            m.source_capacity(self.c,100); storage.assert_called_once()
            with self.assertRaises(RuntimeError): m.source_capacity(self.c,400)

    def test_hdd_capacity_is_reported(self):
        from collections import namedtuple
        usage=namedtuple('usage','total used free')
        gib=1024**3
        with patch.object(m.p.shutil,'disk_usage',side_effect=[usage(100*gib,20*gib,80*gib),usage(100*gib,90*gib,10*gib)]), \
             patch.object(m.p.shutil,'which',return_value=None):
            report=m.p.capacity({**self.c,'instance_pools':{'dev':'hdd'}})
        self.assertTrue(report['review']); self.assertTrue(report['hdd_pause_heavy_work'])
        self.assertEqual(report['hdd']['free_fraction'],.1)

    def test_apply_requires_pause_acknowledgement(self):
        with patch.object(sys,'argv',['storage_migrate.py','move','--config','x','--migration','move1',
                                      '--box','dev','--trial','trial1','--apply']), \
             patch.object(m.p,'config',return_value=self.c),patch.object(m.p,'check_host'), \
             patch.object(m,'move') as move,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'Pause'): m.main()
            move.assert_not_called()

    def test_plan_is_nonmutating(self):
        with patch.object(sys,'argv',['storage_migrate.py','move','--config','x','--migration','move1',
                                      '--box','dev','--trial','trial1']), \
             patch.object(m.p,'config',return_value=self.c),patch.object(m.p,'check_host'), \
             patch.object(m,'move') as move,contextlib.redirect_stdout(io.StringIO()):
            m.main(); move.assert_not_called()

    def test_tool_rollback_refuses_unrelated_edits_and_restores_known_files(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m.p,'STATE',Path(d)):
            directory=Path(d)/'migration'; saved=directory/'backup-tool-before'; saved.mkdir(parents=True)
            installed=Path(d)/'backup-tool'; installed.mkdir()
            hashes={}
            for name in m.TOOL_FILES:
                (saved/name).write_text('old'); (installed/name).write_text('new')
                hashes[name]={'before':m.p.digest(saved/name),'after':m.p.digest(installed/name)}
            (directory/'backup-tool-snapshot.json').write_text(json.dumps(hashes))
            (installed/m.TOOL_FILES[-1]).write_text('foreign')
            with self.assertRaisesRegex(RuntimeError,'outside migration'): m.restore_tool(directory)
            self.assertEqual((installed/m.TOOL_FILES[0]).read_text(),'new')
            (installed/m.TOOL_FILES[-1]).write_text('new'); m.restore_tool(directory)
            self.assertTrue(all((installed/name).read_text()=='old' for name in m.TOOL_FILES))

    def exercise_move(self, failure=None):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            state=Path(d)/'state'; config=Path(d)/'config.json'; config.write_text(json.dumps(self.c))
            before=m.p.instance_spec(self.c,'dev')
            before.update(status='Running'); before['config']['user.chart-box']=m.h.OWNER
            obj=copy.deepcopy(before); events=[]
            def patched(owner,name,**kwargs): return stack.enter_context(patch.object(owner,name,**kwargs))
            patched(m.p,'STATE',new=state); patched(m.h,'host'); patched(m.h,'owned',return_value=obj)
            patched(m.p,'query',return_value=[]); patched(m,'verified_trial',return_value={'path':'fixture','sha256':'hash'})
            patched(m.m,'hdd_capacity'); patched(m.m,'guest',return_value='{"identity":"same"}')
            patched(m.syslog_fix,'nonblocking',return_value=True); patched(m,'timer_active',return_value=True)
            patched(m,'save_tool'); patched(m,'check_installed',return_value='passed')
            patched(m,'source_capacity'); patched(m,'set_config',side_effect=lambda *args:events.append('config'))
            patched(m.h,'wait_ready'); patched(m.m,'validate_move'); patched(m,'wait_identity',return_value={'identity':'same'})
            patched(m,'rootfs_manifest',return_value={'sha256':'hash','apparent_bytes':100})
            patched(m,'export_manifest',return_value={'sha256':'different' if failure=='rootfs' else 'hash'})
            def backed(*args,**kwargs):
                events.append('backup')
                if failure=='backup': raise RuntimeError('backup failed')
                obj['status']='Stopped'; return Path(d)/'backup'
            patched(m.backup,'backup',side_effect=backed)
            def restored(*args): events.append('restore'); return {'checked':True}
            patched(m.backup,'restore',side_effect=restored)
            def transferred(*args):
                events.append('move'); self.assertIn('restore',events)
                self.assertEqual(args[-1],state/'storage-migrations/move1/move-metadata.json')
                obj['devices']['root']['pool']='hdd'
            patched(m.m,'transfer',side_effect=transferred)
            def run(args,**kwargs):
                if args[:2]==['systemctl','stop']: events.append('timer-stop')
                if args[:2]==['systemctl','start']: events.append('timer-start')
                if args[:2]==['incus','start']: events.append('boot')
                if args[0]==sys.executable:
                    events.append('installed-backup'); return str(Path(d)/'backup-after')
                return ''
            patched(m.p,'run',side_effect=run)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            if failure:
                with self.assertRaises(RuntimeError): m.move(self.c,config,'dev','trial1','move1')
            else: m.move(self.c,config,'dev','trial1','move1')
            record=json.loads((state/'storage-migrations/move1/move.json').read_text())
            return events,record

    def test_cutover_order_and_installed_backup_before_boot(self):
        events,record=self.exercise_move()
        self.assertEqual(events,['timer-stop','backup','restore','move','config','installed-backup','restore','boot','timer-start'])
        self.assertEqual(record['phase'],'moved-awaiting-application-and-laptop-acceptance')

    def test_backup_failure_never_moves_or_boots(self):
        events,record=self.exercise_move('backup')
        self.assertEqual(events,['timer-stop','backup'])
        self.assertEqual(record['failed_step'],'stopped-independent-backup')

    def test_rootfs_mismatch_never_registers_or_boots(self):
        events,record=self.exercise_move('rootfs')
        self.assertEqual(events,['timer-stop','backup','restore','move'])
        self.assertEqual(record['failed_step'],'verifying-stopped-rootfs')

    def test_rollback_repairs_metadata_before_move_and_restores_both_configs(self):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            state=Path(d)/'state'; directory=state/'storage-migrations/move1'; directory.mkdir(parents=True)
            config,installed=Path(d)/'config',Path(d)/'installed'
            new={**self.c,'instance_pools':{'dev':'hdd'}}
            config.write_text(json.dumps(new)); installed.write_text(json.dumps(self.c))
            before=m.p.instance_spec(self.c,'dev'); before['status']='Stopped'
            obj=copy.deepcopy(before); obj['devices']['root']['pool']='hdd'
            record={'owner':m.m.OWNER,'host':self.c['machine_id'],'config_path':str(config),
                    'before_config':self.c,'after_config':new,'box':'dev','instance_before':before,
                    'identity_before':{'identity':'same'},'rootfs_before':{'apparent_bytes':100},'timer_was_active':True}
            (directory/'move.json').write_text(json.dumps(record)); events=[]
            def patched(owner,name,**kwargs): return stack.enter_context(patch.object(owner,name,**kwargs))
            patched(m.p,'STATE',new=state); patched(m.p,'HOST_CONFIG',new=installed)
            patched(m.p,'check_host'); patched(m.h,'instance',return_value=obj); patched(m.h,'owned',return_value=obj)
            patched(m.h,'host'); patched(m.h,'wait_ready'); patched(m.m,'validate_move')
            patched(m,'source_capacity'); patched(m,'timer_active',return_value=False)
            patched(m.m,'restore_move_metadata',side_effect=lambda *args:events.append('repair'))
            patched(m.m,'transfer',side_effect=lambda *args:events.append('move'))
            patched(m,'restore_tool',side_effect=lambda *args:events.append('tool'))
            patched(m,'check_installed',side_effect=lambda *args:events.append('check'))
            patched(m,'wait_identity',return_value={'identity':'same'}); patched(m.p,'run')
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            m.rollback(new,config,'move1')
            self.assertEqual(events,['repair','move','tool','check'])
            self.assertEqual(json.loads(config.read_text()),self.c)
            self.assertEqual(json.loads(installed.read_text()),self.c)
            self.assertEqual(json.loads((directory/'move.json').read_text())['phase'],
                             'rolled-back-awaiting-application-acceptance')

    def exercise_resume(self,change=None):
        with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
            state=Path(d)/'state'; directory=state/'storage-migrations/move1'; directory.mkdir(parents=True)
            config=Path(d)/'config'; config.write_text(json.dumps(self.c))
            root=Path(d)/'backups'; exported=root/'stamp/dev'; exported.mkdir(parents=True)
            (exported/'data.tar').write_bytes(b'data'); (exported/'rootfs.tar.gz').write_bytes(b'rootfs')
            before=m.p.instance_spec(self.c,'dev'); before['status']='Running'
            saved={'owner':m.backup.OWNER,'complete':True,'box':'dev','machine_id':self.c['machine_id'],
                   'hdd_uuid':self.c['hdd_uuid'],'instance':before,'data_sha256':m.p.digest(exported/'data.tar'),
                   'rootfs_sha256':m.p.digest(exported/'rootfs.tar.gz')}
            (exported/'backup.json').write_text(json.dumps(saved))
            record={'owner':m.m.OWNER,'host':self.c['machine_id'],'migration':'move1','before_config':self.c,
                    'after_config':{**self.c,'instance_pools':{'dev':'hdd'}},'config_path':str(config),'box':'dev',
                    'instance_before':before,'trial':'trial1','phase':'failed','failed_step':'stopped-independent-backup',
                    'backup':str(exported),'error':'interrupted'}
            obj=copy.deepcopy(before); obj['status']='Stopped'
            if change=='checksum': (exported/'rootfs.tar.gz').write_bytes(b'corrupt')
            if change=='phase': record['failed_step']='moving-rootfs-to-hdd'
            if change=='running': obj['status']='Running'
            receipt=directory/'move.json'; receipt.write_text(json.dumps(record)); original=copy.deepcopy(record)
            def patched(owner,name,**kwargs): return stack.enter_context(patch.object(owner,name,**kwargs))
            patched(m.p,'STATE',new=state); patched(m.h,'host'); patched(m.h,'owned',return_value=obj)
            patched(m,'verified_trial'); patched(m,'check_installed'); patched(m,'timer_active',return_value=False)
            patched(m.backup,'backup_root',return_value=root); patched(m.backup,'storage')
            backup=patched(m.backup,'backup'); finish=patched(m,'finish_move')
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            if change:
                with self.assertRaises(RuntimeError): m.resume_backup(self.c,config,'move1')
                finish.assert_not_called()
            else:
                m.resume_backup(self.c,config,'move1'); finish.assert_called_once()
                self.assertEqual(json.loads((directory/'interrupted-backup.json').read_text()),original)
            backup.assert_not_called()
            self.assertEqual(json.loads((exported/'backup.json').read_text()),saved)

    def test_resume_reuses_verified_backup_and_preserves_interruption(self): self.exercise_resume()

    def test_resume_rejects_modified_archive(self): self.exercise_resume('checksum')

    def test_resume_rejects_later_phase(self): self.exercise_resume('phase')

    def test_resume_rejects_running_box(self): self.exercise_resume('running')


if __name__=='__main__': unittest.main()
