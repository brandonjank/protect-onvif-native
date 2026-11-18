const assert = require("node:assert/strict");
const fs = require("node:fs");

const bundle = fs.readFileSync(process.argv[2], "utf8");
function load(id, dependencies) {
  const start = new RegExp(`(?:^|[,{}])${id}\\(([^()]*)\\)\\{`).exec(bundle);
  assert(start, `Module ${id} was not found`);
  const body = bundle.slice(start.index + start[0].length);
  const end = body.search(/\},\d+\([a-z](?:,[a-z])*\)\{/);
  assert(end >= 0, `Module ${id} has no end marker`);
  const exports = {};
  const requireStub = number => {
    assert(Object.hasOwn(dependencies, number), `Unexpected dependency ${number} from ${id}`);
    return dependencies[number];
  };
  Function(...start[1].split(","), body.slice(0, end))({exports}, exports, requireStub);
  return exports;
}

const shape = fields => ({
  fields, optional() {return this;}, passthrough() {return this;},
  regex() {return this;}, openapi() {return this;},
  and(other) {return shape({...this.fields, ...other.fields});}
});
const z = {
  object: shape, union: () => shape({}), array: () => shape({}),
  string: () => shape({}), number: () => shape({}), unknown: () => shape({}), boolean: () => shape({}),
  any: () => shape({})
};
const log = new Proxy({}, {get: () => () => {}});
const logger = {getLogger: () => log};
const validation = {
  zodToAjvSafeValidateSyncUntitled: () => () => data => ({data, success:true})
};
const events = {emit() {}, async emitAndWait() {}};
const codecs = {isSupportedOnvifEncoding: value => /26[45]/.test(value)};
const levels = {DEVICE_STATE:{CONNECTED:"CONNECTED"}, DeviceModelKey:{CAMERA:"camera"}};
const dummyUser = {id:"admin-fixture"};
const virtualHost = "192.0.2.20";
const actualMac = "020000000001";
const profiles = [
  {token:"a-hq", source:0, width:1920, height:1080, audio:true},
  {token:"a-lq", source:0, width:640, height:360, audio:true},
  {token:"b-hq", source:1, width:1920, height:1080, audio:false},
  {token:"b-lq", source:1, width:640, height:360, audio:false},
  {token:"dual", source:2, width:2560, height:720, audio:true}
];

async function verifyMeasuredBitrate() {
  const camera = (id, bitrate, extra = {}) => ({
    id, isAdopted: true, isThirdPartyCamera: true,
    channels: [{id: 0, enabled: true, bitrate}, {id: 2, enabled: true, bitrate: 0}], ...extra
  });
  const stored = [
    camera("lens-a", null), camera("lens-b", 0), camera("configured", 6144000),
    camera("native", null, {isThirdPartyCamera: false}), camera("empty", null)
  ];
  const deviceSchema = new Proxy({}, {get: () => ({apiSchema: {}, modelKey: "fixture"})});
  const dependencies = Object.fromEntries(
    [103457, 173638, 368498, 607321, 992134, 1328, 43336, 213084, 209882, 625586,
     514118, 632346, 718311, 973102, 97266, 932884, 811338, 278394, 171884, 787461, 413766, 779866]
      .map(id => [id, deviceSchema])
  );
  let bootstrap;
  load(72358, {
    ...dependencies,
    911569: z, 366921: {isCamera: () => true}, 231524: {},
    376995: {publish: async () => undefined},
    477850: {apiComponentRefByName: () => undefined},
    511885: {ContentTypes: {JSON: "json"}}, 182424: {MethodNames: {GET: "get"}},
    478523: {StatusCodes: {OK: 200}},
    139865: {
      getAllDevicesForUser: () => ({cameras: structuredClone(stored)}),
      getAllDevices: () => ({cameras: structuredClone(stored)})
    },
    265207: {defaultJsonResponse: () => undefined},
    815233: class Router { route(route) { bootstrap = route.handlers[0]; } },
    191183: {getClientDetails: () => ({})}, 4054: {isPeerRequest: request => request.peer}
  });
  let queries = 0;
  const db = {
    transaction: txn => txn({
      async query(sql, params) {
        queries++;
        if (!params) {
          assert.match(sql, /statement_timeout/);
          return {};
        }
        assert.match(sql, /SUM\("size"\)::numeric \* 8000/);
        assert.match(sql, /GROUP BY "cameraId"/);
        assert.deepEqual(params.replacements.cameraIds, ["lens-a", "lens-b", "empty"]);
        return {rows: [
          {cameraId: "lens-a", bps: "2505879"}, {cameraId: "lens-b", bps: "3816903"},
          {cameraId: "empty", bps: null}, {cameraId: "configured", bps: "1"}
        ]};
      }
    })
  };
  const context = {
    context: {db, nvr: {apiSerialize: () => ({})}},
    store: {all: () => [], filterByUser: () => []},
    logger: log
  };
  const request = {headers: {}, user: dummyUser};
  let result;
  const response = {json: value => {result = value;}};
  await bootstrap.call(context, request, response);
  assert.deepEqual(result.cameras.map(item => item.channels[0].bitrate), [2505879, 3816903, 6144000, null, null]);
  assert.ok(result.cameras.every(item => item.channels[1].bitrate === 0));
  assert.equal(stored[0].channels[0].bitrate, null);
  assert.equal(queries, 2);
  await bootstrap.call(context, {...request, peer: true}, response);
  assert.equal(result.cameras[0].channels[0].bitrate, null);
  assert.equal(queries, 2);
  context.context.db = {transaction: async () => {throw new Error("unavailable");}};
  await bootstrap.call(context, request, response);
  assert.equal(result.cameras[1].channels[0].bitrate, 0);
  console.log("Bootstrap fills missing third-party HQ bitrate from recording files and preserves configured values");
}

async function run() {
  let soap;
  const parser = load(510144, {
    911569:z, 248543:logger, 477850:validation, 985322:{}, 525511:{asArray: item => Array.isArray(item) ? item : [item]},
    255402:{soapRequest: request => {soap = request; return request.body.GetProfiles.Type
      ? {GetProfilesResponse:{Profiles:profiles.map(profile => rawProfile(profile, true))}}
      : {GetProfilesResponse:{Profiles:profiles.map(profile => rawProfile(profile, false))}};}}
  });
  function rawProfile(profile, media2) {
    const {token, source, width, height, audio} = profile;
    const metadata = {"@_token":token, Name:token};
    const sourceConfig = {"@_token":`configuration-${token}`, SourceToken:source};
    const video = {Resolution:{Width:width, Height:height}, Encoding:"H264", RateControl:{FrameRateLimit:30, BitrateLimit:2048}};
    const audioConfig = {Encoding:"AAC"};
    return media2
      ? {...metadata, Configurations:{VideoSource:sourceConfig, VideoEncoder:video, ...(audio ? {AudioEncoder:audioConfig} : {})}}
      : {...metadata, VideoSourceConfiguration:sourceConfig, VideoEncoderConfiguration:video, ...(audio ? {AudioEncoderConfiguration:audioConfig} : {})};
  }
  const source1 = await parser.fetchProfiles({hostname:virtualHost, port:80, media2:false});
  const source2 = await parser.fetchProfiles({hostname:virtualHost, port:80, media2:true});
  assert.deepEqual(source1, source2);
  assert.deepEqual(source2.map(profile => profile.videoSourceToken), [0,0,1,1,2]);
  assert(soap.body.GetProfiles.Type.includes("VideoSource"));
  console.log("Media1 and Media2 keep source tokens");

  const snapshotRequests = [];
  const connection = {
    createOnvifConnection: async () => ({profiles:source2, mediaPath:"/onvif/media2", ptzPath:"/onvif/ptz"}),
    getDeviceInformation: async () => ({manufacturer:"AXIS", model:"dual sensor test fixture"}),
    getSnapshotUri: async (media, token) => { snapshotRequests.push(token); return `http://camera/snapshot/${token ?? "default"}`; },
    getStreamUri: async (media, token) => `rtsp://camera/${token}`
  };
  const cameraDetails = load(775343, {
    109565:{OnvifError:{}}, 248543:logger, 351399:{replaceUrlHostname:(url, host) => url.replace("://camera", `://${host}`)},
    985322:{}, 465232:connection, 913894:{getNetworkInterfaces:async () => actualMac}
  });
  const ladders = load(270498, {382762:{VideoCodecLabel:{H265:"h265"}},823843:{UnknownError:Error},25739:{},787710:codecs});
  const streamData = load(288221, {270498:ladders});
  const resolveAdoption = load(314204, {667922:levels});
  const records = new Map();
  const store = {
    createCollection() {return [];},
    createRecord(model, fields) {
      if (model === "channel") return fields;
      assert.equal(model, "camera");
      const record = {
        ...fields, pk:`camera-${records.size}`, isNew:true, dirtyAttributes:[], streamingChannels:[],
        async updateAndSave(changes) {
          const info = {...this.thirdPartyCameraInfo, ...changes.thirdPartyCameraInfo};
          Object.assign(this, changes, {thirdPartyCameraInfo:info});
        }
      };
      records.set(fields.mac, record);
      return record;
    },
    findByMac(model, address) {return records.get(address);}
  };
  const clearedConnections = [];
  const subscriber = load(182145, {
    376995:{publish:async (event,mac) => {assert.equal(event, 'onvif.clearConnection'); clearedConnections.push(mac);}},
    80854:{isVideoHandledByAiPort:()=>false}, 667922:levels, 287262:assert,
    43187:{UnauthorizedError:class UnauthorizedError extends Error {}}, 823843:{UnknownError:Error}, 248543:logger, 126176:events,
    465232:connection, 775343:cameraDetails, 510100:{getImagingCapabilities:async ()=>null},
    302555:{getMAC:async ()=>actualMac}, 746377:{}, 288221:streamData, 314204:resolveAdoption, 270498:ladders,
    634459:{queueOnvifStreamRestart:async()=>{}}, 203670:{reconcileOnvifEvents() {}}, 42035:{seedAvailableProfiles:async()=>{}}
  });
  const app = {store};
  const credentials = {host:virtualHost, username:"fixture", password:"fixture", user:dummyUser};
  const probe = await subscriber.probe(app, credentials);
  assert.deepEqual(probe.cameras.map(group => group.streams.map(stream => stream.profileToken)),
    [["a-hq","a-lq"],["b-hq","b-lq"],["dual"]]);
  assert.deepEqual(snapshotRequests, [undefined]);
  const stock = await subscriber.adopt(app, credentials);
  assert.equal(stock.thirdPartyCameraInfo.profileToken, "dual");
  assert.equal(stock.thirdPartyCameraInfo.manualProfileOverride, undefined);
  const first = await subscriber.adopt(app, {...credentials, profileTokens:["a-hq", "a-lq"]});
  const second = await subscriber.adopt(app, {...credentials, profileTokens:["b-hq", "b-lq"], macSalt:"b"});
  assert.equal(first.pk, stock.pk);
  assert.notEqual(second.pk, first.pk);
  assert.equal(first.mac, actualMac);
  assert.equal(second.mac, (await subscriber.adopt(app, {...credentials, profileTokens:["b-hq"], macSalt:"b"})).mac);
  assert.equal(second.thirdPartyCameraInfo.profileTokenLQ, null);
  await subscriber.adopt(app, {...credentials, profileTokens:["b-hq","b-lq"], macSalt:"b"});
  assert.equal(second.thirdPartyCameraInfo.profileToken, "b-hq");
  assert.equal(second.thirdPartyCameraInfo.profileTokenLQ, "b-lq");
  assert.equal(second.thirdPartyCameraInfo.snapshotUrl, `http://${virtualHost}/snapshot/b-hq`);
  assert.equal(second.thirdPartyCameraInfo.manualProfileOverride, true);
  assert.equal(second.thirdPartyCameraInfo.hasAudio, false);
  assert.deepEqual(probe.cameras.map(group => group.videoSourceToken), ['0','1','2']);
  assert(clearedConnections.includes(actualMac));
  assert(clearedConnections.includes(second.mac));
  const count = records.size;
  assert.equal(await subscriber.adopt(app, {...credentials, profileTokens:["no-such-profile"], macSalt:"invalid"}), undefined);
  assert.equal(records.size, count);
  console.log("Adoption groups both lenses, preserves stock behavior, and keeps each lens's profile, snapshot, and identity");

  const manual = await subscriber.adopt(app, {...credentials, macSalt:"manual:other", manualStreams:[
    {uri:"rtsp://192.0.2.21/stream", hasAudio:true}
  ]});
  assert.equal(manual.thirdPartyCameraInfo.profileToken, "manual-0");
  assert.equal(manual.thirdPartyCameraInfo.rtspUrl, "rtsp://192.0.2.21/stream");
  assert.equal(manual.featureFlags.isPtz, false);
  assert.equal(manual.featureFlags.hasMic, true);
  const sync = load(652303, {
    80854:{isVideoHandledByAiPort:()=>false}, 376995:{}, 248543:logger, 126176:events, 510100:{},
    111260:{mergeOnvifErrors:()=>[], onvifErrorsEqual:()=>true}, 655932:{}, 288221:streamData, 787710:codecs, 270498:ladders
  });
  const state = {...app, onvifImagingReprobeAt:new Map([[second.pk, Date.now()]]),
    onvifCapabilityProbeState:new Map([[second.pk, profiles.map(profile=>profile.token).sort().join(",")]])};
  const latest = await cameraDetails.getCameraDetails(virtualHost, "80", "", "");
  await sync.syncOnvifCameraConfig(state, second, latest);
  await sync.syncOnvifCameraConfig({}, manual, latest);
  assert.equal(second.thirdPartyCameraInfo.rtspUrl, `rtsp://${virtualHost}/b-hq`);
  assert.equal(second.thirdPartyCameraInfo.snapshotUrl, `http://${virtualHost}/snapshot/b-hq`);
  assert.equal(manual.thirdPartyCameraInfo.rtspUrl, "rtsp://192.0.2.21/stream");
  console.log("Periodic sync preserves selected lenses and manual stream URLs");

  const discovered = load(896684, {
    667922:levels, 376995:{publish:async()=>{}}, 287262:assert, 248543:logger, 606250:{LogLevelKey:{}},
    126176:events, 775343:cameraDetails, 288221:streamData
  });
  const moved = "192.0.2.22";
  await discovered.onvifCameraDiscovered(app, {mac:actualMac, hostname:moved, port:"80", name:"moved camera"});
  assert.equal(first.thirdPartyCameraInfo.rtspUrl, `rtsp://${moved}/a-hq`);
  assert.equal(first.thirdPartyCameraInfo.rtspUrlLQ, `rtsp://${moved}/a-lq`);
  assert.equal(first.thirdPartyCameraInfo.snapshotUrl, `http://${moved}/snapshot/a-hq`);
  console.log("IP rediscovery refreshes selected profile URLs and snapshot");

  const permission = () => {};
  const granularPermission = () => {};
  const payloadValidation = () => {};
  class Router {
    constructor() { this.routes = []; }
    route(route) { this.routes.push(route); }
  }
  let requested;
  const result = {name:"probe result"};
  const router = load(276141, {
    660349:{Type:{Object:()=>({}), String:()=>({})}}, 911569:{z},
    992134:{CameraDecalModel:{modelKey:"camera"}}, 973102:{NvrDecalModel:{modelKey:"nvr"}},
    231524:{AccessMode:{CREATE:"create",WRITE:"write"}}, 672311:{GranularAccess:{SETTINGS_EDIT:"edit"}},
    376995:{publish:async (...args)=>{requested = args; return result;}}, 176216:{NotFoundError:Error},
    477850:{apiComponent:(name, value)=>value, typeboxToAjvUntitled:()=>{}, apiComponentRef:()=>{}},
    815087:{createPayloadValidator:()=>payloadValidation, createPayloadAjvValidator:()=>{}},
    265207:{defaultJsonResponse:()=>{}}, 815233:Router, 511885:{ContentTypes:{JSON:"json"}},
    182424:{MethodNames:{POST:"post", GET:"get"}}, 478523:{StatusCodes:{OK:200,BAD_REQUEST:400,FORBIDDEN:403,NOT_FOUND:404}},
    139865:{}, 969669:{validateGranularUserPermission:()=>granularPermission}, 85277:{validateUserPermission:()=>permission},
    973024:{readFileSync(){throw new Error("Use embedded HTML fixture");}}
  }).thirdPartyCamerasRouter;
  const probeRoute = router.routes.find(route => route.path.endsWith("/probe"));
  const helperRoute = router.routes.find(route => route.path.endsWith("/onvif-helper"));
  assert.deepEqual(probeRoute.handlers.slice(0, 3), [permission, granularPermission, payloadValidation]);
  assert.deepEqual(helperRoute.handlers.slice(0, 2), [permission, granularPermission]);
  let reply;
  await probeRoute.handlers.at(-1)({body:credentials, user:dummyUser}, {json(value){reply=value;}});
  assert.equal(reply, result);
  assert.equal(requested[0], "thirdPartyCameras.probe");
  assert.deepEqual(requested[1], credentials);
  await helperRoute.handlers.at(-1)({}, {
    setHeader(name,value) {assert.equal(value,"text/html; charset=utf-8");},
    status(code) {assert.equal(code,200); return this;},
    end(value) {assert(value.includes("<html"));}
  });
  console.log("Helper and probe routes register with the same camera permission checks as stock adoption");
  await verifyMeasuredBitrate();
}

run().catch(error => {console.error(error); process.exitCode=1;});
