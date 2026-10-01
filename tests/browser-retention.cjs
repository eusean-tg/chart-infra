const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(path.join(process.env.HOME,'.local/share/chart-infra/browser-driver/node_modules/playwright'));
const dir=path.join(process.env.HOME,'.local/state/chart-infra/browser');
(async()=>{
 const browser=await chromium.launch({headless:true,proxy:{server:'http://127.0.0.1:9',bypass:'localhost,127.0.0.1'},args:['--use-gl=angle','--use-angle=swiftshader']});
 try {
  const old=JSON.parse(fs.readFileSync(path.join(dir,'session-private.json')));
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block',storageState:{cookies:old.cookies,origins:[]}});
  await context.route('**/*',r=>['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname)?r.continue():r.abort());
  const page=await context.newPage();const responses=[];
  page.on('response',r=>{if(r.url().includes('/api/'))responses.push({path:new URL(r.url()).pathname,status:r.status(),method:r.request().method()})});
  await page.goto('http://127.0.0.1:8097/chart/',{waitUntil:'domcontentloaded',timeout:120000});
  await page.getByTestId('profile-avatar-btn').waitFor({timeout:60000});
  await page.locator('[data-testid^="workspace-tab-"][aria-current="page"]').filter({hasText:'Shared dev offline fixture'}).waitFor({timeout:60000});
  await context.storageState({path:path.join(dir,'session-private.json')});fs.chmodSync(path.join(dir,'session-private.json'),0o600);
  for(const text of ['Got it','Skip']){const b=page.getByText(text,{exact:true}).first();const box=await b.count()?await b.boundingBox():null;if(box&&box.y>=0&&box.y<950)await b.click();}
  if(!responses.some(r=>r.path.endsWith('/auth-v2/refresh-token')&&r.status===200))throw Error('No successful cookie refresh');
  if(!responses.some(r=>r.path.includes('/workspaces')&&r.status===200&&r.method==='GET'))throw Error('No successful database layout read');
  const result={passed:true,browser:browser.version(),offline_wrapper:true,fresh_browser_without_local_storage:true,session_restored_from_cookies:true,retained_layout_visible:true,layout:'Shared dev offline fixture',responses};
  await context.storageState({path:path.join(dir,'session-private.json')});fs.chmodSync(path.join(dir,'session-private.json'),0o600);
  await page.screenshot({path:path.join(dir,'layout-after-runtime-recreation.png')});
  fs.writeFileSync(path.join(dir,'retention.json'),JSON.stringify(result,null,2));
  console.log('PASS: fresh browser refreshed session and loaded retained layout after app/Mongo recreation.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
