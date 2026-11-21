// ES5 MIDI Remote driver. Control ports are separate from note input.
var api = require('midiremote_api_v1');
var driver = api.makeDeviceDriver('LocalStudio', 'CubasePiaproMCP', 'LocalStudio');
var input = driver.mPorts.makeMidiInput('CP MCP To Cubase');
var output = driver.mPorts.makeMidiOutput('CP MCP From Cubase');
driver.makeDetectionUnit().detectPortPair(input, output)
    .expectInputNameContains('CP MCP To Cubase')
    .expectOutputNameContains('CP MCP From Cubase');
var page = driver.mMapping.makePage('Data-first MCP');
var active = false;
var session = '';
var cache = {playing:127, recording:127, volume:127, pan:127, mute:127, solo:127};
function bind(name, cc, host, button) {
    var control = button ? driver.mSurface.makeButton(cc, 0, 1, 1) : driver.mSurface.makeKnob(cc, 1, 1, 1);
    control.mSurfaceValue.mMidiBinding.setInputPort(input).bindToControlChange(15, cc);
    var commands = {playing:'Start', recording:'Record', stopTrigger:'Stop'};
    if (commands[name]) {
        page.makeCommandBinding(control.mSurfaceValue, 'Transport', commands[name]).filterByValue(1);
        page.makeValueBinding(driver.mSurface.makeCustomValueVariable(name + ' host feedback'), host);
    } else {
        page.makeValueBinding(control.mSurfaceValue, host).setTypeDefault().setValueTakeOverModeJump();
    }
    host.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
        cache[name] = Math.max(0, Math.min(126, Math.round(value * 126)));
    };
}
var transport = page.mHostAccess.mTransport.mValue;
var channel = page.mHostAccess.mTrackSelection.mMixerChannel;
bind('playing', 20, transport.mStart, true);
bind('stopTrigger', 21, transport.mStop, true);
bind('recording', 22, transport.mRecord, true);
bind('volume', 30, channel.mValue.mVolume, false);
bind('pan', 31, channel.mValue.mPan, false);
bind('mute', 32, channel.mValue.mMute, true);
bind('solo', 33, channel.mValue.mSolo, true);
var query = driver.mSurface.makeCustomValueVariable('MCP state query');
query.mMidiBinding.setInputPort(input).bindToControlChange(15, 119);
query.mOnProcessValueChange = function (activeDevice, value) {
    if (!active) { return; }
    var nonce = Math.round(value * 127);
    output.sendMidi(activeDevice, [240,125,67,80,2,nonce,cache.playing,cache.recording,
        cache.volume,cache.pan,cache.mute,cache.solo,247]);
};

// Query callbacks never dereference native host handles. Only populated host
// callbacks read IDs, using the mapping context passed to that same callback.
function hostID(host, activeMapping, title) {
    if (!title || !activeMapping) { return ''; }
    try {
        var uid = host.getUniqueIDString(activeMapping);
        return typeof uid === 'string' ? uid : '';
    } catch (error) { return ''; }
}
function clearEntry(entry) {
    entry.name = '';
    entry.uid = '';
    entry.plugin = '';
    entry.selected = false;
}
function cacheIdentity(host, entry) {
    host.mOnTitleChange = function (activeDevice, activeMapping, title) {
        if (!title) { clearEntry(entry); return; }
        entry.name = title;
        entry.uid = hostID(host, activeMapping, title);
    };
    host.mOnColorChange = function (activeDevice, activeMapping, r, g, b, a, isActive) {
        if (!isActive) { clearEntry(entry); return; }
        if (entry.name) { entry.uid = hostID(host, activeMapping, entry.name); }
    };
}
var selected = {name:'', uid:'', plugin:'', selected:false};
cacheIdentity(channel, selected);
channel.mInstrumentPluginSlot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name) {
    selected.plugin = name;
};
var editorOpen = 127;
var openEditor = driver.mSurface.makeCustomValueVariable('MCP open instrument editor');
openEditor.mMidiBinding.setInputPort(input).bindToControlChange(15, 115);
page.makeValueBinding(openEditor, channel.mValue.mInstrumentOpen).filterByValue(1);
channel.mValue.mInstrumentOpen.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
    editorOpen = Math.round(value);
};

var bank = page.mHostAccess.mMixConsole.makeMixerBankZone('MCP track inventory');
bank.includeAudioChannels().includeInstrumentChannels().includeSamplerChannels().includeMIDIChannels();
bank.setFollowVisibility(false);
var trackCache = [];
function setupTrack(slot) {
    var host = bank.makeMixerBankChannel();
    var entry = {slot:slot, name:'', uid:'', selected:false, plugin:''};
    trackCache.push(entry);
    cacheIdentity(host, entry);
    var selector = driver.mSurface.makeCustomValueVariable('MCP track ' + slot);
    if (slot < 64) { selector.mMidiBinding.setInputPort(input).bindToControlChange(14, slot); }
    page.makeValueBinding(selector, host.mValue.mSelected).filterByValue(1);
    host.mValue.mSelected.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
        entry.selected = value > 0.5;
    };
    page.makeValueBinding(driver.mSurface.makeCustomValueVariable('MCP plugin ' + slot), host.mInstrumentPluginSlot.mOn);
    host.mInstrumentPluginSlot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name) {
        entry.plugin = name;
    };
}
for (var slot = 0; slot < 65; ++slot) { setupTrack(slot); }

// MIDI tracks expose the destination's plugin identity too. These filtered
// banks cache owner/type membership without ever reading empty native slots.
var typeCache = {instrument:[], midi:[]};
function setupTypeBank(kind) {
    var zone = page.mHostAccess.mMixConsole.makeMixerBankZone('MCP ' + kind + ' identity');
    if (kind === 'instrument') { zone.includeInstrumentChannels(); }
    else { zone.includeMIDIChannels(); }
    zone.setFollowVisibility(false);
    for (var index = 0; index < 64; ++index) {
        var entry = {name:'', uid:''};
        typeCache[kind].push(entry);
        cacheIdentity(zone.makeMixerBankChannel(), entry);
    }
}
setupTypeBank('instrument');
setupTypeBank('midi');
function trackKind(uid) {
    if (!uid) { return 'unknown'; }
    for (var kind in typeCache) {
        for (var index = 0; index < typeCache[kind].length; ++index) {
            if (typeCache[kind][index].uid === uid) { return kind; }
        }
    }
    return 'audio_or_sampler';
}
var resetBank = driver.mSurface.makeCustomValueVariable('MCP reset inventory bank');
resetBank.mMidiBinding.setInputPort(input).bindToControlChange(15, 117);
page.makeActionBinding(resetBank, bank.mAction.mResetBank).filterByValue(1);
var commands = [
    [100, 'AddTrack', 'Audio'], [101, 'AddTrack', 'Instrument'],
    [102, 'AddTrack', 'MIDI'], [104, 'File', 'Save'], [105, 'File', 'Save As']
];
for (var commandIndex = 0; commandIndex < commands.length; ++commandIndex) {
    var spec = commands[commandIndex];
    var trigger = driver.mSurface.makeCustomValueVariable('MCP ' + spec[1] + ' ' + spec[2]);
    trigger.mMidiBinding.setInputPort(input).bindToControlChange(15, spec[0]);
    page.makeCommandBinding(trigger, spec[1], spec[2]).filterByValue(1);
}
var direct = typeof page.mHostAccess.makeDirectAccess === 'function'
    ? page.mHostAccess.makeDirectAccess(channel) : null;
var directMapping = null;
var directMeter = null;
page.mOnActivate = function (device, mapping) {
    active = true;
    session = String(new Date().getTime()) + '-' + String(Math.random());
    if (direct && mapping) {
        try {
            direct.activate(mapping);
            directMapping = mapping;
        } catch (error) { directMapping = null; }
    }
};
page.mOnDeactivate = function () {
    if (directMapping) {
        try { direct.deactivate(directMapping); } catch (error) {}
    }
    directMapping = null;
    directMeter = null;
    active = false;
    clearEntry(selected);
    editorOpen = 127;
    for (var index = 0; index < trackCache.length; ++index) { clearEntry(trackCache[index]); }
    for (var kind in typeCache) {
        for (var item = 0; item < typeCache[kind].length; ++item) { clearEntry(typeCache[kind][item]); }
    }
};

// DirectAccess is an optional extension; older Cubase and existing clients
// can continue using the transport and inventory protocol above.
if (direct && channel.mValue.mVUMeter) {
    page.makeValueBinding(driver.mSurface.makeCustomValueVariable('MCP selected channel meter'), channel.mValue.mVUMeter);
    channel.mValue.mVUMeter.mOnProcessValueChange = function (device, mapping, value) {
        if (isFinite(value) && selected.uid) {
            directMeter = {uid:selected.uid, value:value, sampled_at_ms:new Date().getTime()};
        }
    };
}

function directObject(id) {
    return {
        id:id, title:direct.getObjectTitle(directMapping, id),
        uid:direct.getObjectUniqueIDString(directMapping, id),
        name:direct.getObjectUniqueName(directMapping, id),
        type:direct.getObjectTypeName ? direct.getObjectTypeName(directMapping, id) : '',
        children:direct.getNumberOfChildObjects(directMapping, id),
        parameters:direct.getNumberOfParameters(directMapping, id)
    };
}

function directParameter(id, tag) {
    return {tag:tag, title:direct.getParameterTitle(directMapping, id, tag, 128),
        value:direct.getParameterProcessValue(directMapping, id, tag),
        display:direct.getParameterDisplayValue(directMapping, id, tag),
        units:direct.getParameterDisplayUnits(directMapping, id, tag)};
}

function directSnapshot(id, request) {
    var current = directObject(id);
    var offset = request.offset || 0;
    var index;
    var tag;
    current.child_page = [];
    current.parameter_page = [];
    for (index = offset; index < Math.min(offset + 24, current.children); ++index) {
        current.child_page.push(directObject(direct.getChildObjectID(directMapping, id, index)));
    }
    for (index = offset; index < Math.min(offset + 24, current.parameters); ++index) {
        tag = direct.getParameterTagByIndex(directMapping, id, index);
        current.parameter_page.push(directParameter(id, tag));
    }
    return current;
}

function directTarget(request, base) {
    var id = base;
    for (var index = 0; index < request.path.length; ++index) {
        var child = request.path[index];
        if (child < 0 || child >= direct.getNumberOfChildObjects(directMapping, id)) {
            throw new Error('OBJECT_PATH_CHANGED');
        }
        id = direct.getChildObjectID(directMapping, id, child);
    }
    // An object ID on its own cannot authorize writes after a selection,
    // slot replacement, or host reload.
    if (request.expected_id !== undefined) {
        var object = directObject(id);
        if (object.id !== request.expected_id || object.uid !== request.expected_uid ||
            object.name !== request.expected_name || object.type !== request.expected_type) {
            throw new Error('OBJECT_IDENTITY_CHANGED');
        }
    }
    return id;
}

function directPluginCollection(id) {
    var manager = direct.mPluginManager;
    if (!manager || !manager.getIndexOfDefaultPluginCollection) { throw new Error('PLUGIN_API_UNAVAILABLE'); }
    var index = manager.getIndexOfDefaultPluginCollection(directMapping, id);
    var count = manager.getNumberOfPluginCollections(directMapping, id);
    if (index < 0 || index >= count) { throw new Error('PLUGIN_COLLECTION_UNAVAILABLE'); }
    var collection = manager.getPluginCollectionByIndex(directMapping, id, index);
    if (!collection || !collection.mEntries) { throw new Error('PLUGIN_COLLECTION_UNAVAILABLE'); }
    return collection;
}

function directRun(request) {
    if (!active || request.session !== session || !selected.uid || request.track_uid !== selected.uid) {
        throw new Error('TRACK_SELECTION_CHANGED');
    }
    if (!directMapping) { throw new Error('DIRECT_ACCESS_UNAVAILABLE'); }
    if (request.op === 'meter') {
        if (!directMeter || directMeter.uid !== selected.uid) { throw new Error('METER_SAMPLE_UNAVAILABLE'); }
        return directMeter;
    }
    var base = direct.getBaseObjectID(directMapping);
    if (!isFinite(base) || base < 0) { throw new Error('DIRECT_ACCESS_UNAVAILABLE'); }
    var id = directTarget(request, base);
    if (request.op === 'inspect') { return directSnapshot(id, request); }
    if (request.op === 'plugin_catalog') {
        var collection = directPluginCollection(id);
        var start = request.offset || 0;
        var catalog = [];
        for (var i = start; i < Math.min(start + 32, collection.mEntries.length); ++i) {
            var item = collection.mEntries[i];
            catalog.push({uid:item.mPluginUID, name:item.mPluginName, vendor:item.mPluginVendor});
        }
        return {plugins:catalog, total:collection.mEntries.length, collection:collection.mName};
    }
    if (request.op === 'parameter_set' || request.op === 'parameter_read') {
        var found = false;
        var count = direct.getNumberOfParameters(directMapping, id);
        for (var j = 0; j < count; ++j) {
            if (direct.getParameterTagByIndex(directMapping, id, j) === request.tag) { found = true; break; }
        }
        if (!found) { throw new Error('PARAMETER_UNAVAILABLE'); }
        var before = directParameter(id, request.tag);
        if (before.title !== request.expected_title) { throw new Error('PARAMETER_CHANGED'); }
        if (request.op === 'parameter_read') { return before; }
        if (request.expected_id === undefined || cache.playing !== 0 || cache.recording !== 0) {
            throw new Error('MUTATION_REQUIRES_STOPPED_IDENTIFIED_TARGET');
        }
        if (!isFinite(request.value) || request.value < 0 || request.value > 1 ||
            !isFinite(request.expected_value) || !isFinite(before.value) ||
            Math.abs(before.value - request.expected_value) > 0.00001) {
            throw new Error('PARAMETER_CHANGED');
        }
        direct.setParameterProcessValue(directMapping, id, request.tag, request.value);
        return {before:before, after:directParameter(id, request.tag), dispatched:true};
    }
    if (request.op === 'plugin_load') {
        if (request.expected_id === undefined || cache.playing !== 0 || cache.recording !== 0) {
            throw new Error('MUTATION_REQUIRES_STOPPED_IDENTIFIED_TARGET');
        }
        var plugins = directPluginCollection(id).mEntries;
        if (!plugins.some(function (plugin) { return plugin.mPluginUID === request.plugin_uid; })) {
            throw new Error('PLUGIN_NOT_IN_COLLECTION');
        }
        if (direct.getNumberOfChildObjects(directMapping, id) !== 0) { throw new Error('PLUGIN_SLOT_NOT_EMPTY'); }
        return {dispatched:direct.mPluginManager.trySetSlotPlugin(directMapping, id, request.plugin_uid, false)};
    }
    throw new Error('UNKNOWN_DIRECT_COMMAND');
}

function sendDirectReply(device, nonce, result) {
    var text = JSON.stringify(result);
    var parts = Math.ceil(text.length / 180);
    if (parts > 80) { text = '{"error":"DIRECT_REPLY_TOO_LARGE"}'; parts = 1; }
    for (var part = 0; part < parts; ++part) {
        var data = [240,125,67,80,7,nonce,part,parts];
        for (var i = part * 180; i < Math.min(text.length, (part + 1) * 180); ++i) {
            var code = text.charCodeAt(i);
            data.push(code & 127, (code >> 7) & 127, (code >> 14) & 3);
        }
        data.push(247);
        output.sendMidi(device, data);
    }
}

input.mOnSysex = function (device, message) {
    var start = message[0] === 240 ? 1 : 0;
    var end = message.length - (message[message.length - 1] === 247 ? 1 : 0);
    if (end - start < 7 || end - start > 850 || message[start] !== 125 ||
        message[start + 1] !== 67 || message[start + 2] !== 80 || message[start + 3] !== 6) { return; }
    var nonce = message[start + 4];
    var text = '';
    for (var i = start + 5; i < end; ++i) { text += String.fromCharCode(message[i]); }
    try {
        sendDirectReply(device, nonce, {result:directRun(JSON.parse(text))});
    } catch (error) { sendDirectReply(device, nonce, {error:String(error.message || error)}); }
};
var versionQuery = driver.mSurface.makeCustomValueVariable('MCP inventory version query');
versionQuery.mMidiBinding.setInputPort(input).bindToControlChange(15, 116);
versionQuery.mOnProcessValueChange = function (activeDevice, value) {
    var nonce = Math.round(value * 127);
    if (nonce) { output.sendMidi(activeDevice, [240,125,67,80,5,nonce,1,247]); }
};
var inventoryQuery = driver.mSurface.makeCustomValueVariable('MCP inventory query');
inventoryQuery.mMidiBinding.setInputPort(input).bindToControlChange(15, 118);
inventoryQuery.mOnProcessValueChange = function (activeDevice, value) {
    var nonce = Math.round(value * 127);
    if (!nonce) { return; }
    if (!active) {
        output.sendMidi(activeDevice, [240,125,67,80,4,nonce,1,247]);
        return;
    }
    var tracks = [];
    for (var index = 0; index < 64; ++index) {
        var entry = trackCache[index];
        if (entry.name) {
            tracks.push({slot:index, name:entry.name, uid:entry.uid, kind:trackKind(entry.uid),
                selected:entry.selected, plugin:entry.plugin});
        }
    }
    var snapshot = {session:session, capacity:64, overflow:!!trackCache[64].name, driver_build:'host-cache-1',
        selected:{name:selected.name, uid:selected.uid, plugin:selected.plugin, editor_open:editorOpen}, tracks:tracks};
    var text = JSON.stringify(snapshot);
    var parts = Math.ceil(text.length / 200);
    if (parts > 127) { return; }
    for (var part = 0; part < parts; ++part) {
        var message = [240,125,67,80,3,nonce,part,parts];
        var end = Math.min(text.length, (part + 1) * 200);
        for (var position = part * 200; position < end; ++position) {
            var code = text.charCodeAt(position);
            message.push(code & 127, (code >> 7) & 127, (code >> 14) & 3);
        }
        message.push(247);
        output.sendMidi(activeDevice, message);
    }
};
