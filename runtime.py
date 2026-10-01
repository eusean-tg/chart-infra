#!/usr/bin/env python3
"""Build and publish the development toolchain to the PC's loopback registry."""
import argparse
import json
from pathlib import Path
from common import guard, lock, run, save

ROOT=Path(__file__).resolve().parent
TAG='chart-infra-node:24.20.0-pnpm11.28.2'
REMOTE='localhost:5000/chart-infra/node:24.20.0-pnpm11.28.2'

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['build']);p.parse_args()
 guard();held=lock('chart-runtime-build')
 run(['docker','build','-t',TAG,'-f',str(ROOT/'runtime/Dockerfile'),str(ROOT/'runtime')])
 run(['docker','tag',TAG,REMOTE]);run(['docker','push',REMOTE])
 obj=json.loads(run(['docker','image','inspect',REMOTE],capture=True))[0]
 digest=next(d for d in obj['RepoDigests'] if d.startswith('localhost:5000/chart-infra/node@'))
 versions=run(['docker','run','--rm','--network','none',obj['Id'],'sh','-c','node --version; pnpm --version; dpkg-query -W'],capture=True)
 save('node-runtime.json',{'node':'24.20.0','pnpm':'11.28.2','docker_image':obj['Id'],'cluster_image':digest,'packages':versions})
 print('Pinned runtime recorded. Reinstall changed dependencies explicitly before deploying.')

if __name__=='__main__':main()
