// Optional image/PDF export. Requires Playwright and an installed Chrome browser.
// The SVG itself is generated with Python's standard library.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {chromium} = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const docs = path.join(root, 'docs', 'contracts');
  const svg = fs.readFileSync(path.join(docs, 'model-flow-overview.svg'), 'utf8');
  const plan = JSON.parse(fs.readFileSync(path.join(docs, 'model-flow-map.v0.1.json'), 'utf8'));
  const width = Number(svg.match(/\bwidth="([\d.]+)"/)[1]);
  const height = Number(svg.match(/\bheight="([\d.]+)"/)[1]);
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:Math.ceil(width),height:Math.ceil(height)},deviceScaleFactor:2});
    const errors=[];
    page.on('pageerror', error=>errors.push(String(error)));
    await page.setContent(`<!doctype html><html lang="tr"><head><meta charset="utf-8"><style>
      html,body { margin:0; padding:0; background:white; }
      svg { display:block; }
      @page { size:A3 landscape; margin:6mm; }
      @media print { svg { width:100%; height:auto; max-height:280mm; } }
      </style></head><body>${svg.replace(/<\?xml[^>]*\?>/, '')}</body></html>`);
    await page.evaluate(()=>document.fonts.ready);
    const defects = await page.evaluate(() => {
      const defects=[];
      const nodes=[...document.querySelectorAll('.node')];
      for (const group of nodes) {
        const rect=group.querySelector('rect').getBBox();
        for(const label of group.querySelectorAll('text')) {
          const box=label.getBBox();
          if(box.x<rect.x-1 || box.x+box.width>rect.x+rect.width+1 || box.y<rect.y-1 || box.y+box.height>rect.y+rect.height+1)
            defects.push({node:group.id,text:label.textContent,issue:'label outside node'});
        }
      }
      for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++) {
        const a=nodes[i].querySelector('rect').getBBox();
        const b=nodes[j].querySelector('rect').getBBox();
        if(Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x)>1 && Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y)>1)
          defects.push({nodes:[nodes[i].id,nodes[j].id],issue:'overlapping nodes'});
      }
      const inside = (point,box) => point.x>=box.x-1 && point.x<=box.x+box.width+1 && point.y>=box.y-1 && point.y<=box.y+box.height+1;
      for(const edge of document.querySelectorAll('path[data-from][data-to]')) {
        const from=document.getElementById(edge.dataset.from)?.querySelector('rect');
        const to=document.getElementById(edge.dataset.to)?.querySelector('rect');
        if(!from || !to || !inside(edge.getPointAtLength(0),from.getBBox()) || !inside(edge.getPointAtLength(edge.getTotalLength()),to.getBBox()))
          defects.push({from:edge.dataset.from,to:edge.dataset.to,issue:'edge endpoint outside declared node'});
        if(edge.dataset.model && edge.dataset.field) {
          const expected=(edge.dataset.role==='review' ? 'output-' : 'model-')+edge.dataset.model;
          if(edge.dataset.to!==expected) defects.push({field:edge.dataset.field,issue:'wrong role destination'});
        }
      }
      return defects;
    });
    const actualFields = await page.locator('[data-field-id],[data-field-ids]').evaluateAll(nodes=>nodes.flatMap(n=>n.dataset.fieldIds ? JSON.parse(n.dataset.fieldIds) : [n.dataset.fieldId]).sort());
    if(JSON.stringify(actualFields)!==JSON.stringify(Object.keys(plan.fields).sort())) defects.push({issue:'missing or duplicate fields'});
    const actualModels=await page.locator('g[data-kind="model"]').evaluateAll(nodes=>nodes.map(n=>n.id.replace('model-','')).sort());
    if(JSON.stringify(actualModels)!==JSON.stringify(plan.models.map(m=>m.id).sort())) defects.push({issue:'missing or duplicate models'});
    const actualEdges=await page.locator('path[data-model][data-field]').evaluateAll(nodes=>nodes.filter(n=>n.dataset.model&&n.dataset.field).map(n=>[n.dataset.model,n.dataset.field,n.dataset.role].join(':')).sort());
    const expectedEdges=plan.models.flatMap(m=>m.inputs.map(([id,role])=>[m.id,id,role].join(':'))).sort();
    if(JSON.stringify(actualEdges)!==JSON.stringify(expectedEdges)) defects.push({issue:'model edge mismatch'});
    if(defects.length || errors.length) throw new Error(JSON.stringify({defects,errors},null,2));
    await page.screenshot({path:path.join(docs,'model-flow-overview.png'),fullPage:true});
    await page.pdf({path:path.join(docs,'model-flow-overview.pdf'),printBackground:true,preferCSSPageSize:true});
    const artifacts={};
    for(const extension of ['png','pdf']) {
      const buffer=fs.readFileSync(path.join(docs,'model-flow-overview.'+extension));
      artifacts[extension]={sha256:crypto.createHash('sha256').update(buffer).digest('hex'),size_bytes:buffer.length};
    }
    const proof={svg_sha256:crypto.createHash('sha256').update(svg).digest('hex'),artifacts,fields:actualFields.length,models:plan.models.length,modelConnections:actualEdges.length,layoutDefects:defects,runtimeErrors:errors,trainingExecuted:false};
    fs.writeFileSync(path.join(docs,'model-flow-overview.exports.json'),JSON.stringify(proof,null,2)+'\n');
    process.stdout.write(`OK: ${actualFields.length} fields, ${plan.models.length} models, ${actualEdges.length} connections; no clipped labels or overlapping nodes. PNG and A3 PDF exported.\n`);
  } finally {
    await browser.close();
  }
})().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
