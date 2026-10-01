const {chromium}=require(process.env.HOME+'/.local/share/chart-infra/browser-driver/node_modules/playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,proxy:{server:'http://127.0.0.1:9',bypass:'localhost,127.0.0.1'},args:['--use-gl=angle','--use-angle=swiftshader']});
 try {
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
 const blocked=new Set();
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname)){blocked.add(u.origin);return r.abort();}return r.continue();});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8097/chart/',{waitUntil:'domcontentloaded',timeout:120000});
 await page.waitForTimeout(15000);
 for (const label of ['Got it', 'Continue Anyway']) {
   const b=page.getByRole('button',{name:label,exact:true}); const box=await b.boundingBox(); if(box && box.y>=0 && box.y<950) await b.click();
 }
 await page.getByTestId('profile-guest-mode-btn').click();
 await page.waitForTimeout(2000);
 await page.locator('input[type="email"][name="email"]').fill('sean.shared-dev@example.test');
 const codeResponse=page.waitForResponse(r=>r.url().includes('/login-code/request'));
 await page.getByTestId('login-signup-submit-btn').click();
 const cr=await codeResponse; if(cr.status()!==200)throw Error('Code request status '+cr.status());
 await page.getByTestId('login-code-input').first().waitFor({timeout:30000});
 const {execFileSync}=require('node:child_process');
 const logs=execFileSync('kubectl',['-n','chart-sean','logs','deployment/auth','--since=2m'],{encoding:'utf8'});
 const codes=[...logs.matchAll(/sign-in code for sean\.shared-dev@example\.test: (\d{6})/g)];
 if(!codes.length) throw Error('No local code was logged');
 await page.getByTestId('login-code-input').first().fill(codes.at(-1)[1]);
 await page.waitForTimeout(2000);
 const username=page.getByTestId('login-code-username-input');
 if(await username.isVisible()) { await username.fill('chartdevsean'); await page.getByTestId('login-code-create-btn').click(); }
 await page.getByTestId('profile-avatar-btn').waitFor({timeout:60000});
 await page.waitForTimeout(1500);
 await context.storageState({path:process.env.HOME+'/.local/state/chart-infra/browser/session-private.json'});
 fs.chmodSync(process.env.HOME+'/.local/state/chart-infra/browser/session-private.json',0o600);
 console.log((await page.locator('body').innerText()).slice(0,4500));
 console.log('INPUTS',await page.locator('input').evaluateAll(xs=>xs.map(x=>({type:x.type,placeholder:x.placeholder,testid:x.dataset.testid}))));
 console.log('BUTTONS',await page.locator('button').evaluateAll(xs=>xs.filter(x=>x.getBoundingClientRect().width).map(x=>({text:x.innerText,testid:x.dataset.testid,aria:x.getAttribute('aria-label')})).slice(0,35)));
 console.log('ERRORS',errors.slice(0,6));console.log('EXTERNAL ORIGINS BLOCKED',Array.from(blocked));
 await page.screenshot({path:process.env.HOME+'/.local/state/chart-infra/browser/login-screen.png'});
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
