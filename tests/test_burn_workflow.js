// Drive the actual output buttons, confirmation, and /api/jobs request without disc writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../app/index.html'), 'utf8');
const outputButtons = html.match(/<div class="button-row">(.*?)<\/div>/s)[1];
const primaryId = outputButtons.match(/<button class="[^"]*\bprimary-button\b[^"]*" id="([^"]+)"/)[1];
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', listeners: {}, open: false, dataset: {}, style: {},
    classList: {toggle() {}}, setAttribute() {}, focus() {},
    addEventListener(event, listener) { this.listeners[event] = listener; },
    showModal() { this.open = true; },
    close() { this.open = false; this.listeners.close?.(); },
  });
  return elements.get(id);
}
const requests = [];
const context = vm.createContext({
  document: {getElementById: element, querySelector: () => ({value: 'dvd-video'})},
  setTimeout() {}, clearTimeout() {}, requests,
});
vm.runInContext(source.slice(0, source.indexOf('\n$("add-files").addEventListener')), context);
vm.runInContext(source.slice(source.indexOf('$("prepare-button").addEventListener'), source.indexOf('$("cancel-job").addEventListener')), context);
vm.runInContext(`
  state={files:[{id:'movie'}],devices:{drives:[{id:'writer',name:'DVD writer',letter:'F:',blank:true,writable:true}]}};
  plan={can_start:true};
  api=async(path,options)=>{requests.push({path,...options});return {};};
  loadState=async()=>{}; navigate=()=>{}; updateControls=()=>{};
`, context);
element('recorder').value = 'writer';
element('disc-label').value = 'MOVIE';

async function clickAndConfirm(id) {
  await element(id).listeners.click();
  if (element('burn-dialog').open) await element('burn-confirm').listeners.click();
  await new Promise(setImmediate);
}

(async () => {
  await clickAndConfirm('burn-button');
  assert.equal(requests.pop().body.action, 'burn', 'Explicit burn must reach the burn pipeline');
  await clickAndConfirm(primaryId);
  assert.equal(requests.pop().body.action, 'burn', 'The primary DVD action must burn the selected blank disc, rather than stop at an ISO');
  assert.match(outputButtons, /Save ISO only/, 'Export must explicitly say it only saves an ISO');
  await clickAndConfirm('prepare-button');
  assert.equal(requests.pop().body.action, 'prepare', 'Explicit ISO export must not write a disc');
  element('burn-button').listeners.click();
  element('burn-cancel').listeners.click();
  assert.equal(requests.length, 0, 'Cancelling confirmation must not start a job');
  assert.equal(element('burn-dialog').open, false);
  console.log('PASS: primary DVD burn, explicit ISO-only export, confirmation cancellation');
})().catch(error => { console.error(error); process.exitCode = 1; });
