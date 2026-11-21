// Exercise the real driver with host handles that reject reads of empty slots.
// This is a native-boundary contract, not a mock of Cubase's audio engine.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const values = new Map(), zones = [], messages = [], invalidReads = [];
let idReads = 0, inHostCallback = false;
function value(name) {
    const result = {mMidiBinding: {
        setInputPort() { return this; }, bindToControlChange() { return this; }
    }};
    if (name) values.set(name, result);
    return result;
}
function host() {
    return {
        assigned: null,
        mValue: Object.fromEntries(['Volume','Pan','Mute','Solo','Selected','InstrumentOpen']
            .map(name => ['m' + name, value()])),
        mInstrumentPluginSlot: {mOn: value()},
        getUniqueIDString() {
            idReads++;
            if (!this.assigned || !inHostCallback) {
                invalidReads.push(this.assigned ? 'outside-host-callback' : 'empty-slot');
                throw new Error(invalidReads.at(-1));
            }
            return this.assigned.uid;
        }
    };
}
const selected = host();
let mapped = false, processValue = .5;
const direct = {
    activate() { mapped = true; },
    deactivate() { mapped = false; },
    getBaseObjectID() { assert.ok(mapped); return 7; },
    getObjectTitle() { return "Track"; },
    getObjectUniqueIDString() { return "node-uid"; },
    getObjectUniqueName() { return "node-name"; },
    getObjectTypeName() { return "Channel"; },
    getNumberOfChildObjects() { return 0; },
    getNumberOfParameters() { return 1; },
    getParameterTagByIndex() { return 42; },
    getParameterTitle() { return "Pan"; },
    getParameterProcessValue() { return processValue; },
    getParameterDisplayValue() { return String(processValue); },
    getParameterDisplayUnits() { return ""; },
    setParameterProcessValue(mapping, id, tag, value) {
        assert.equal(id, 7);
        assert.equal(tag, 42);
        processValue = value;
    }
};
function binding() {
    return {filterByValue() { return this; }, setTypeDefault() { return this; },
        setValueTakeOverModeJump() { return this; }};
}
const page = {
    mHostAccess: {
        mTransport: {mValue: {mStart:value(), mStop:value(), mRecord:value()}},
        mTrackSelection: {mMixerChannel:selected},
        makeDirectAccess() { return direct; },
        mMixConsole: {makeMixerBankZone() {
            const zone = {kinds:[], hosts:[], mAction:{mResetBank:{}},
                includeAudioChannels() { this.kinds.push('audio'); return this; },
                includeInstrumentChannels() { this.kinds.push('instrument'); return this; },
                includeSamplerChannels() { this.kinds.push('sampler'); return this; },
                includeMIDIChannels() { this.kinds.push('midi'); return this; },
                setFollowVisibility() { return this; },
                makeMixerBankChannel() { const h=host(); this.hosts.push(h); return h; }};
            zones.push(zone); return zone;
        }}
    }, makeValueBinding:binding, makeCommandBinding:binding, makeActionBinding:binding
};
const driver = {
    mPorts: {makeMidiInput() { this.input = {}; return this.input; }, makeMidiOutput() {
        return {sendMidi(device, bytes) { messages.push(bytes); }};
    }},
    makeDetectionUnit() { return {detectPortPair() { return this; },
        expectInputNameContains() { return this; }, expectOutputNameContains() { return this; }}; },
    mSurface: {makeButton() { return {mSurfaceValue:value()}; },
        makeKnob() { return {mSurfaceValue:value()}; }, makeCustomValueVariable:value},
    mMapping: {makePage() { return page; }}
};
const context = {require() { return {makeDeviceDriver() { return driver; }}; }};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const mapping = {};
function emit(fn, ...args) {
    inHostCallback = true;
    try { if (fn) fn({}, mapping, ...args); } finally { inHostCallback = false; }
}
function assign(h, track) {
    h.assigned = track || null;
    emit(h.mOnTitleChange, track?.name || '');
    emit(h.mOnColorChange, 0,0,0,0, !!track);
    emit(h.mInstrumentPluginSlot.mOnChangePluginIdentity, track?.plugin || '');
    emit(h.mValue.mSelected.mOnProcessValueChange, track?.selected ? 1 : 0);
}
function project(tracks) {
    emit(page.mOnActivate);
    for (const zone of zones) {
        const filtered = tracks.filter(t => zone.kinds.includes(t.kind));
        zone.hosts.forEach((h,i) => assign(h, filtered[i]));
    }
    assign(selected, tracks.find(t => t.selected));
}
function snapshot() {
    messages.length = 0;
    const before = idReads;
    values.get('MCP inventory query').mOnProcessValueChange({}, 41/127);
    assert.equal(idReads, before, 'MIDI query must serialize cached metadata without native host reads');
    assert.equal(invalidReads.length, 0, 'Never dereference an unassigned native channel');
    const frames = messages.filter(x => x[4]===3).sort((a,b) => a[6]-b[6]);
    assert.ok(frames.length, 'Expected inventory frames, including for empty projects');
    let text='';
    for (const frame of frames) {
        for (let i=8; i<frame.length-1; i+=3) {
            text += String.fromCharCode(frame[i] | frame[i+1]<<7 | frame[i+2]<<14);
        }
    }
    return JSON.parse(text);
}
project([]);
assert.deepEqual(snapshot().tracks, []);
const midi={uid:'midi-1',name:'Route 🐳',kind:'midi',plugin:'Piapro Studio VSTi',selected:true};
project([midi]);
let result=snapshot();
assert.equal(result.tracks[0].uid,'midi-1');
assert.equal(result.tracks[0].kind,'midi');
assert.equal(result.selected.uid,'midi-1');
const instrument={uid:'owner-1',name:'Piapro',kind:'instrument',plugin:'Piapro Studio VSTi',selected:false};
project([instrument,midi]);
result=snapshot();
assert.deepEqual(result.tracks.map(t=>t.kind), ['instrument','midi']);
// No selected channel, deleted tracks, a project with identical names but new IDs.
project([instrument]);
assert.equal(snapshot().selected.uid,'');
project([]);
assert.equal(snapshot().tracks.length,0);
emit(page.mOnDeactivate);
project([{...midi,uid:'midi-new-project'}]);
assert.equal(snapshot().tracks[0].uid,'midi-new-project');
for (let i=0;i<100;i++) snapshot();

function directRequest(request) {
    messages.length = 0;
    const bytes = [240,125,67,80,6,11,...Buffer.from(JSON.stringify(request),"ascii"),247];
    driver.mPorts.input.mOnSysex({},bytes);
    const frames = messages.filter(x => x[4] === 7).sort((a,b) => a[6]-b[6]);
    let response = '';
    for (const frame of frames) {
        for (let i=8; i<frame.length-1; i+=3) {
            response += String.fromCharCode(frame[i] | frame[i+1]<<7 | frame[i+2]<<14);
        }
    }
    return JSON.parse(response);
}
const current = snapshot();
const address = {session:current.session, track_uid:current.selected.uid, path:[]};
assert.equal(directRequest({...address, op:"inspect"}).result.parameter_page[0].value, .5);
const edit = {...address, op:"parameter_set", tag:42, expected_title:"Pan", expected_value:.5, value:.6,
    expected_id:7, expected_uid:"node-uid", expected_name:"node-name", expected_type:"Channel"};
assert.equal(directRequest(edit).error, "MUTATION_REQUIRES_STOPPED_IDENTIFIED_TARGET");
emit(page.mHostAccess.mTransport.mValue.mStart.mOnProcessValueChange,0);
emit(page.mHostAccess.mTransport.mValue.mRecord.mOnProcessValueChange,0);
assert.equal(directRequest({...edit, expected_uid:"wrong"}).error, "OBJECT_IDENTITY_CHANGED");
assert.equal(directRequest(edit).result.after.value, .6);
assert.equal(directRequest(edit).error, "PARAMETER_CHANGED");
emit(page.mOnDeactivate);
assert.equal(mapped,false);
assert.equal(directRequest(edit).error, "TRACK_SELECTION_CHANGED");
console.log('Host-boundary lifecycle contract passed');
