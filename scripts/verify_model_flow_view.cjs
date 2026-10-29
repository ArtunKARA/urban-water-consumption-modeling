// Browser regression checks for the saved research planning view.
// Optional tooling: Playwright + installed Chrome; no application data is read.
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const {chromium} = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const docs = path.join(root, 'docs', 'contracts');
  const plan = JSON.parse(fs.readFileSync(path.join(docs,'model-flow-map.v0.1.json'),'utf8'));
  const proofDir = path.join(root,'var','proofs','model-flow-map-audit');
  fs.mkdirSync(proofDir,{recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:736,height:1000}});
    const errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(pathToFileURL(path.join(docs,'model-flow-map.html')).href);
    await page.locator('#flow-model-node').waitFor();
    for(const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for(const width of [736,320]) {
        await page.setViewportSize({width,height:1000});
        for(const model of plan.models) {
          await page.locator('#uw-flow-model').selectOption(model.id);
          if(!(await page.locator('#flow-model-node').innerText()).includes(model.id)) throw Error('wrong selected model');
          const fields=await page.locator('#uw-flow-graph [data-field-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.fieldId).sort());
          const expected=model.inputs.filter(([,role])=>role!=='review').map(([id])=>id).sort();
          if(JSON.stringify(fields)!==JSON.stringify(expected)) throw Error('missing/duplicate/input review field: '+model.id);
          for(const [id,role] of model.inputs) {
            const selector=role==='review' ? '#uw-flow-review' : '#uw-flow-graph';
            if(await page.locator(`${selector} [data-field-id="${id}"][data-role="${role}"]`).count()!==1) throw Error('incorrect field role: '+id);
          }
          await page.locator('#uw-flow-graph [data-field-select]').first().click();
          if(!(await page.locator('#uw-flow-selection').innerText()).includes('Bağlantılar:')) throw Error('field selection failed');
          const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
          if(overflow) throw Error('horizontal overflow '+width+' '+theme+' '+model.id);
          const duplicates=await page.evaluate(()=>{
            const ids=[...document.querySelectorAll('[id]')].map(n=>n.id);
            return ids.filter((id,i)=>ids.indexOf(id)!==i);
          });
          if(duplicates.length) throw Error('duplicate DOM ids');
        }
      }
    }
    await page.setViewportSize({width:736,height:1000});
    await page.locator('#uw-flow-model').selectOption('F01');
    await page.locator('[data-field-select="weather_issued"]').first().click();
    if(!(await page.locator('#uw-flow-selection').innerText()).includes('Yalnız:')) throw Error('conditional weather gate is hidden');
    await page.locator('.flow-plan summary').click();
    if(!(await page.locator('#uw-flow-plan').innerText()).includes('Yalnız geçmiş ve takvim')) throw Error('history-only variant missing');
    await page.locator('.flow-plan summary').click();
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('openai:set_globals',{
      detail:{globals:{widgetState:{modelContent:{model_id:'F02',field_id:'__proto__'}}}}
    })));
    if(!(await page.locator('#flow-model-node').innerText()).includes('F02')) throw Error('state restore failed');
    if((await page.locator('#uw-flow-selection').innerText()).includes('__proto__')) throw Error('inherited field accepted');
    await page.locator('#uw-flow-model').selectOption('A01');
    await page.emulateMedia({colorScheme:'light'});
    await page.screenshot({path:path.join(proofDir,'view-736.png'),fullPage:true});
    if(errors.length) throw Error(errors.join('\n'));
    fs.writeFileSync(path.join(proofDir,'browser.json'),JSON.stringify({
      models:plan.models.map(m=>m.id),widths:[736,320],themes:['light','dark'],
      checks:['exact input sets','review isolation','field roles','selection','horizontal layout',
        'unique DOM IDs','conditional gates','base variants','invalid saved field'],
      errors,trainingExecuted:false
    },null,2)+'\n');
    process.stdout.write('OK: all model fields/roles, 2 widths × 2 themes, conditional gates, and saved-state regression.\n');
  } finally {
    await browser.close();
  }
})().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
