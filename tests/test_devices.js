// Exercise frontend selection, destination, and status policies using Node's standard library.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
let format = 'dvd-video';
function mockElement() {
  return {
    value: '', textContent: '', dataset: {}, style: {}, children: [], listeners: {}, attributes: {},
    classList: {toggle() {}},
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); },
    prepend(...children) { this.children.unshift(...children); },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; if (name === 'value') delete this.value; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    closest() { return this; }
  };
}
function element(id) {
  if (!elements.has(id)) elements.set(id, mockElement());
  return elements.get(id);
}
const context = vm.createContext({
  document: {getElementById: element, querySelector: () => ({value: format}), querySelectorAll: () => [], createElement: mockElement, createElementNS: mockElement},
  setTimeout: () => 0, clearTimeout() {},
  Option: class {constructor(text, value) {this.textContent = text; this.value = value;}}
});
const source = fs.readFileSync(path.join(__dirname, '../app/app.js'), 'utf8');
const bindingStart = source.indexOf('\n$("add-files").addEventListener');
assert.ok(bindingStart > 0, 'Frontend event binding boundary must exist');
vm.runInContext(source.slice(0, bindingStart), context);
const run = (script) => vm.runInContext(script, context);
const state = (drives = [], volumes = []) => run(`state=${JSON.stringify({
  devices: {drives, volumes}, files: [{id: 'test-file'}], job: null, default_output: 'D:\\exports'
})};online=true;planning=false;plan={can_start:true};renderDiagnostics=()=>{};`);
const dvd = {id: 'dvd', letter: 'F:', can_write: true, supports_dvd: true, physical_media_type: 10, blank: true, writable: true};
const cd = {...dvd, id: 'cd', letter: 'E:', supports_dvd: false, physical_media_type: 2};
const usb = {letter: 'G:', drive_type: 3, is_usb: true, is_system: false, read_only: false, filesystem: 'exFAT', capacity_bytes: 1000, free_bytes: 900};

state([cd, dvd]);
assert.equal(run('bestRecorder(state.devices.drives,"dvd-video").id'), 'dvd');
state([cd, {...dvd, blank: false, writable: false}]);
assert.equal(run('bestRecorder(state.devices.drives,"data-disc").id'), 'cd');
assert.equal(run('bestRecorder([{can_write:false,writable:false}],"dvd-video")'), null);

format = 'usb-original';
state([], [usb, {...usb, letter: 'C:', is_system: true}, {...usb, letter: 'H:', read_only: true}, {...usb, letter: 'I:', drive_type: 2, is_usb: false}]);
assert.equal(run('usbVolumes().map(v=>v.letter).join(",")'), 'G:');
state([], [{...usb, letter: 'H:'}]);
run('manualUSB="G:";outputEdited=false;renderDestination();updateControls();');
assert.equal(element('output-dir').value, 'G:\\');
assert.equal(run('destinationUnavailable()'), true);
assert.equal(element('prepare-button').disabled, true);
state([], [usb, {...usb, letter: 'H:'}]);
run('renderDestination();updateControls();');
assert.equal(element('output-dir').value, 'G:\\');
assert.equal(run('destinationUnavailable()'), false);
assert.equal(element('prepare-button').disabled, false);

element('output-dir').value = 'D:\\my-folder';
run('outputEdited=true;manualUSB=null;renderDestination();');
assert.equal(element('output-dir').value, 'D:\\my-folder');

format = 'dvd-video';
element('disc-label').value = 'LUMABURN';
state([dvd]);
run('manualRecorder="missing-recorder";renderDevices();updateControls();');
assert.equal(element('recorder').value, 'missing-recorder');
assert.equal(element('burn-button').disabled, true);
assert.equal(element('prepare-button').disabled, false);
assert.equal(run('requestBody().recorder_id'), null);
state([dvd, {...dvd, id: 'missing-recorder', letter: 'H:'}]);
run('renderDevices();');
assert.equal(element('recorder').value, 'missing-recorder');
run('manualRecorder="";renderDevices();');
assert.equal(element('recorder').value, '');
assert.equal(run('discWriteProtected({phase:"preparing",noninterruptible:false})'), false);
assert.equal(run('discWriteProtected({phase:"writing",noninterruptible:true})'), true);

// Newly added files are included; a checkbox excludes a file from totals and request bodies.
run('state.files=[{id:"a",name:"first.mp4",size:1000,duration:60},{id:"b",name:"second.mp4",size:2000,duration:120}];renderFiles();');
assert.equal(run('requestBody().file_ids.join(",")'), 'a,b');
assert.equal(element('queue-total').textContent, '2 selected · 3 KB · 3:00');
const firstCheckbox = element('file-list').children[0].children[0];
assert.equal(firstCheckbox.checked, true);
firstCheckbox.checked = false;
firstCheckbox.listeners.change();
assert.equal(run('requestBody().file_ids.join(",")'), 'b');
assert.equal(element('queue-total').textContent, '1 selected · 2 KB · 2:00');
assert.equal(element('output-queue-summary').textContent, element('queue-total').textContent);
assert.equal(element('select-all').indeterminate, true);
run('renderFiles();'); // Polls retain the user's checkbox choice.
assert.equal(run('requestBody().file_ids.join(",")'), 'b');
run('state.files.push({id:"c",name:"new.mp4",size:1000,duration:60});renderFiles();');
assert.equal(run('requestBody().file_ids.join(",")'), 'b,c');
run('excludedFiles.add("b");excludedFiles.add("c");renderSelection();plan={can_start:true};planning=false;updateControls();');
assert.equal(run('requestBody().file_ids.length'), 0);
assert.equal(element('prepare-button').disabled, true);
assert.equal(element('queue-total').textContent, 'No files selected');
run('state.files=state.files.filter(file=>file.id!=="a");renderFiles();');
assert.equal(run('excludedFiles.has("a")'), false);
assert.equal(run('requestBody().performance'), 'gentle');
element('performance').value = 'full';
assert.equal(run('requestBody().performance'), 'full');

assert.equal(run('volumeName({letter:"C:",label:"Unknown volume"})'), 'C:');
assert.equal(run('volumeName({letter:"C:",label:"Unnamed volume"})'), 'C:');
assert.equal(run('volumeName({letter:"G:",label:"Movies"})'), 'G: · Movies');
run('state.tools={};renderCompression();');
assert.equal(element('compression-engine').value, 'lzma');
assert.equal(element('open-7z').hidden, true);
run('state.tools={"7z":true,winrar:true};renderCompression();');
assert.equal(element('compression-engine').children.map(option=>option.value).join(','), '7z,winrar,lzma');
assert.equal(element('open-winrar').hidden, false);
run('state.compression={state:"completed",total_bytes:1000,estimated_bytes:500,saved_bytes:500,saved_percent:50,sampled_bytes:200,file_count:1};renderCompression();');
assert.equal(element('compression-estimated').textContent, '500 bytes');
assert.equal(element('compression-saved').textContent, '500 bytes (50.0%)');
run('state.job={id:"job",state:"running",phase:"writing",progress:65,phase_progress:10};renderJob();');
assert.equal(element('job-percent').textContent, '65%');
assert.equal(element('job-phase-percent').textContent, '10% of phase');
run('state.job.progress=null;state.job.phase_progress=null;renderJob();');
assert.equal(element('job-percent').textContent, 'Progress unknown');
assert.equal(element('job-progress').value, undefined);
assert.equal(element('job-phase-percent').textContent, 'Progress unknown');
run('testResult={summary:"Structure readable",scope:"folder",note:"Playback untested",checks:[{name:"Structure",passed:true,message:"Readable"},{name:"Drive",passed:false,message:"Missing"}]};renderTests();');
assert.equal(element('tests-summary').textContent, 'Structure readable');
assert.equal(element('tests-checks').children[0].children[0].textContent, 'Passed · Structure');
assert.equal(element('tests-checks').children[1].children[0].textContent, 'Failed · Drive');
assert.equal(element('tests-limitations').textContent, 'Playback untested');
run('state.job=null;state.compression=null;state.setup={state:"running",message:"Downloading media tools",progress:42};renderDependencies();updateControls();');
assert.equal(element('setup-notice').hidden, false);
assert.equal(element('setup-progress').value, 42);
assert.equal(element('retry-setup').hidden, true);
assert.equal(element('dependency-notice').hidden, true);
assert.equal(element('add-files').disabled, true);
assert.equal(element('prepare-button').disabled, true);
run('state.setup={state:"failed",message:"Download failed",progress:null};renderDependencies();updateControls();');
assert.equal(element('setup-notice').hidden, false);
assert.equal(element('setup-message').textContent, 'Download failed');
assert.equal(element('retry-setup').hidden, false);
assert.equal(element('retry-setup').disabled, false);
assert.equal(element('add-files').disabled, false);
run('state.setup={state:"completed"};state.tools={ffmpeg:true,ffprobe:true,dvdauthor:true};renderDependencies();');
assert.equal(element('setup-notice').hidden, true);
assert.equal(element('dependency-notice').hidden, true);
console.log('PASS: destinations, selected files/totals, performance, compression, progress, disc checks, automatic setup and retry');
