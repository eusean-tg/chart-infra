const {chromium}=require(process.env.HOME+'/.local/share/chart-infra/browser-driver/node_modules/playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,proxy:{server:'http://127.0.0.1:9',bypass:'localhost,127.0.0.1'},args:['--use-gl=angle','--use-angle=swiftshader']});
 try {
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block',storageState:process.env.HOME+'/.local/state/chart-infra/browser/session-private.json'});
 const blocked=new Set();
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname)){blocked.add(u.origin);return r.abort();}return r.continue();});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8097/chart/',{waitUntil:'domcontentloaded',timeout:120000});
 const requests=[];page.on('response',r=>{if(r.url().includes('/api/'))requests.push({url:new URL(r.url()).pathname,status:r.status()})});
 await page.waitForTimeout(30000);
 const skip=page.getByText('Skip',{exact:true});if(await skip.isVisible())await skip.click();
 await page.getByTestId('profile-avatar-btn').waitFor({timeout:30000});
 const active=page.locator('[data-testid^="workspace-tab-"][aria-current="page"]');
 await active.dblclick();
 const rename=page.getByTestId('workspace-tab-rename-input');
 await rename.fill('Shared dev offline fixture');
 const saved=page.waitForResponse(r=>r.url().includes('/workspaces/') && ['PATCH','PUT'].includes(r.request().method()) && r.status()===200,{timeout:30000});
 await rename.press('Enter'); await saved;
 await page.waitForTimeout(1500);
 await context.storageState({path:process.env.HOME+'/.local/state/chart-infra/browser/session-private.json'});
 fs.chmodSync(process.env.HOME+'/.local/state/chart-infra/browser/session-private.json',0o600);
 console.log('LAYOUT SAVE ACKNOWLEDGED',await active.innerText());
 console.log('API RESPONSES',requests);

 console.log((await page.locator('body').innerText()).slice(0,10000));
 console.log('INPUTS',await page.locator('input').evaluateAll(xs=>xs.map(x=>({type:x.type,placeholder:x.placeholder,testid:x.dataset.testid}))));
 console.log('BUTTONS',await page.locator('button').evaluateAll(xs=>xs.filter(x=>x.getBoundingClientRect().width).map(x=>({text:x.innerText,testid:x.dataset.testid,aria:x.getAttribute('aria-label')})).slice(0,35)));
 console.log('ERRORS',errors.slice(0,6));console.log('EXTERNAL ORIGINS BLOCKED',Array.from(blocked));
 await page.screenshot({path:process.env.HOME+'/.local/state/chart-infra/browser/layout-saved.png'});
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
