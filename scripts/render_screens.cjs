/* Capture rendered lesson screens without invoking teaching/demo commands. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {pathToFileURL} = require('node:url');
const packagePath = process.env.PLAYWRIGHT_MODULE || 'playwright';
const {chromium} = require(packagePath);
if (!process.argv[2]) throw Error('Usage: node render_screens.cjs WORK_DIRECTORY');
const work = path.resolve(process.argv[2]);
// A failed recapture must not leave an older successful validation usable.
fs.rmSync(path.join(work,'screen-validation.json'),{force:true});
const plan = JSON.parse(fs.readFileSync(path.join(work,'timeline.json'),'utf8'));
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1,reducedMotion:'reduce'});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    fs.mkdirSync(path.join(work,'screens'),{recursive:true});
    for(let i=0;i<plan.segments.length;i++){
      await page.goto(pathToFileURL(path.join(work,'screens.html')).href+'?screen='+i);
      await page.waitForFunction(()=>document.documentElement.dataset.ready==='true');
      const overflow=await page.evaluate(()=>{
        const content=document.getElementById('content');
        const code=document.querySelector('.code');
        const horizontal=['content','title','label','brand','chapter'].filter(id=>{
          const element=document.getElementById(id),box=element.getBoundingClientRect();
          return element.scrollWidth>element.clientWidth+2||box.left<0||box.right>1600;
        });
        return {bottom:content.getBoundingClientRect().bottom,clipped:code&&code.scrollHeight>code.clientHeight+2,horizontal};
      });
      if(overflow.bottom>748||overflow.clipped||overflow.horizontal.length)errors.push('Screen does not fit: '+plan.segments[i].id+' '+JSON.stringify(overflow));
      await page.screenshot({path:path.join(work,'screens',plan.segments[i].id+'.png')});
      if(i%10===0)console.log('SCREEN',i+1,'/',plan.segments.length);
    }
    if(errors.length)throw Error(errors.join('\n'));
    const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(path.join(work,file))).digest('hex');
    fs.writeFileSync(path.join(work,'screen-validation.json'),JSON.stringify({
      timelineSha256:hash('timeline.json'),htmlSha256:hash('screens.html'),
      screens:Object.fromEntries(plan.segments.map(s=>[s.id,hash('screens/'+s.id+'.png')]))
    },null,2)+'\n');
    console.log('SCREENS_READY',plan.segments.length);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
