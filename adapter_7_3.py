"""ONVIF adoption adapter carried forward from the verified Protect 7.3.74 patch."""

import json
from pathlib import Path

HTML = Path(__file__).with_name("onvif_helper.html").read_text(encoding="utf-8")

SALTED_MAC = r'''(()=>{const z=String(e)+"|"+String(t.macSalt);let q=2166136261;for(let i=0;i<z.length;i++){q=(q^z.charCodeAt(i))>>>0,q=Math.imul(q,16777619)>>>0}let w=1540483477;for(let i=z.length-1;i>=0;i--){w=(w^z.charCodeAt(i))>>>0,w=Math.imul(w,16777619)>>>0}const b=[q>>>24&255,q>>>16&255,q>>>8&255,q&255,w>>>8&255,w&255];return b[0]=b[0]&254|2,b.map(x=>x.toString(16).padStart(2,"0")).join("").toUpperCase()})()'''

ADOPT_7_3 = 't.adopt=async(e,t)=>{const{store:r}=e,{username:o,password:d}=t;try{let A,I,R;'

RECORDED_BITRATE = '''
async function fillMissingRecordingBitrates(db, cameras, logger) {
  const missing = new Map();
  for (const camera of cameras) {
    if (!camera.isThirdPartyCamera || !camera.isAdopted || !camera.id) continue;
    const high = camera.channels?.find(channel => channel.id === 0 && channel.enabled);
    if (high && !(high.bitrate > 0)) missing.set(camera.id, high);
  }
  if (!missing.size) return;
  try {
    const {rows} = await db.transaction(async txn => {
      await txn.query("SET LOCAL statement_timeout = '1500ms'");
      return txn.query(`
        SELECT "cameraId",
          ROUND(SUM("size")::numeric * 8000 / NULLIF(SUM("end" - "start"), 0)) AS "bps"
        FROM "recordingFiles"
        WHERE "cameraId" = ANY(ARRAY[:cameraIds])
          AND "channel" = 0 AND "type" = 'rotating' AND "active" = false
          AND "start" >= :windowStart AND "size" > 0
          AND ("end" - "start") >= 60000
          AND "size" / NULLIF("end" - "start", 0) < 25000
        GROUP BY "cameraId"
      `, {replacements: {
        cameraIds: [...missing.keys()],
        windowStart: Date.now() - 6 * 60 * 60 * 1000
      }});
    });
    for (const row of rows) {
      const bps = Number(row.bps), high = missing.get(row.cameraId);
      if (high && Number.isFinite(bps) && bps > 0 && bps <= 200_000_000) high.bitrate = bps;
    }
  } catch {
    logger.warn("Could not load measured recording bitrate for the Protect bootstrap");
  }
}
'''


def edits():
    changes = []

    def replace(label, old, new):
        changes.append((label, old, new))

    # Media2 returns only the configuration types named in GetProfiles.
    replace("7.3 request Media2 video source",
            'Type:["VideoEncoder","AudioEncoder"]', 'Type:["VideoEncoder","AudioEncoder","VideoSource"]')
    media2_audio = 'audioEncoderConfiguration:(null===(i=e.Configurations)||void 0===i?void 0:i.AudioEncoder)?P(e.Configurations.AudioEncoder):void 0'
    replace("7.3 Media2 video source", media2_audio + '}},t,r)',
            media2_audio + ',videoSourceToken:e.Configurations?.VideoSource?.SourceToken??e.Configurations?.VideoSource?.["@_token"]}},t,r)')
    media1_audio = 'audioEncoderConfiguration:e.AudioEncoderConfiguration?P(e.AudioEncoderConfiguration):void 0'
    replace("7.3 Media1 video source", media1_audio + '}},t,r)',
            media1_audio + ',videoSourceToken:e.VideoSourceConfiguration?.SourceToken??e.VideoSourceConfiguration?.["@_token"]}},t,r)')

    stream_uri = 'const o=(0,a.replaceUrlHostname)(await(0,s.getStreamUri)(m,r.token),e);'
    stream = 'v.push({resolution:i.resolution,encoding:i.encoding,quality:i.quality,bitrate:(0,t.normalizeBitrate)(i.rateControl.bitrateLimit),fps:i.rateControl.frameRateLimit,uri:o,hasAudio:n,profileToken:r.token})'
    snapshot = '''let __snapshot;
        if (u?.snapshotProfileTokens?.includes(r.token)) {
          try { __snapshot = (0,a.replaceUrlHostname)(await (0,s.getSnapshotUri)(m,r.token),e); }
          catch { /* Fall back to the camera's default snapshot. */ }
        }
        '''
    replace("7.3 stream profile and snapshot", stream_uri + stream,
            stream_uri + snapshot + stream[:-2] + ',profileName:r.name,videoSourceToken:r.videoSourceToken,snapshotUri:__snapshot})')

    # Keep separate helpers inside the existing ONVIF subscriber module.
    probe = '''const __publishOnvif = r(376995).publish;
    const __seedAdoptedProfiles = async (camera, selected) => {
      if (selected && camera.mac) {
        await __publishOnvif("onvif.clearConnection", camera.mac).catch(error => {
          T.debug(`Failed to clear the ONVIF connection for ${camera.logId}`, error);
        });
      }
      return (0,_.seedAvailableProfiles)(camera,{overwriteExisting:!0});
    };
    t.probe = async (e, request) => {
      const {username, password, camera, host} = request;
      const [hostname, port = "80"] = camera
        ? [camera.host, camera.thirdPartyCameraInfo.port]
        : host.split(":");
      n.default.ok(hostname, "Missing camera host");
      n.default.ok(port, "Missing camera port");
      const details = await (0,p.getCameraDetails)(hostname, String(port), username, password, {});
      if (!details.success) throw new s.UnauthorizedError({details:details.errors});
      const groups = new Map();
      for (const stream of details.streams) {
        if (!/26[45]/.test(stream.encoding)) continue;
        const rawSource = stream.videoSourceToken;
        const source = rawSource == null || rawSource === "" ? null : String(rawSource);
        if (!groups.has(source)) groups.set(source, []);
        groups.get(source).push({
          profileToken:stream.profileToken, profileName:stream.profileName || stream.profileToken,
          encoding:stream.encoding, width:stream.resolution.width, height:stream.resolution.height,
          fps:stream.fps, bitrate:stream.bitrate, hasAudio:!!stream.hasAudio
        });
      }
      const cameras = Array.from(groups, ([videoSourceToken, streams], index) => ({
        videoSourceToken,
        label:videoSourceToken ? details.name + " #" + (index+1) : details.name,
        streams:streams.sort((first, second) => second.width-first.width)
      }));
      return {name:details.name, ptz:!!details.ptz, success:true, errors:details.errors,
        host:hostname, port:String(port), cameras};
    };
    '''
    replace("7.3 probe subscriber", ADOPT_7_3, probe + ADOPT_7_3)
    subscriber = '"thirdPartyCameras.adopt":d.adopt,'
    replace("7.3 subscriber registry", subscriber, subscriber + '"thirdPartyCameras.probe":d.probe,')

    # Preserve the existing identity for previously adopted lenses.
    selection = '''const __manual = t.manualStreams?.length > 0;
    const __selected = t.profileTokens?.length > 0;
    const __override = __manual || __selected;
    const __details = __manual ? {...C, ptz:false, streams:t.manualStreams.map((stream,index) => ({
      resolution:{width:stream.width || (index ? 640 : 1920), height:stream.height || (index ? 480 : 1080)},
      encoding:stream.encoding || "h264", quality:"", bitrate:stream.bitrate > 0 ? stream.bitrate : (index ? 512 : 2048),
      fps:stream.fps > 0 ? stream.fps : 30, uri:stream.uri, hasAudio:!!stream.hasAudio,
      profileToken:"manual-" + index
    }))} : __selected ? {...C, streams:C.streams.filter(stream => t.profileTokens.includes(stream.profileToken))} : C;
    const b=(0,h.getStreamData)(__details);'''
    replace("7.3 probe selected profile snapshots", 'o,d,{fetchHwAddress:!0});if(T.info(`Adopting camera at',
            'o,d,{fetchHwAddress:!0,snapshotProfileTokens:t.profileTokens});if(T.info(`Adopting camera at')
    replace("7.3 selected streams", 'const b=(0,h.getStreamData)(C);', selection)
    identity = '''N=__manual ? null : M.snapshotUri ?? C.snapshotUri;
      const P=await (0,g.getMAC)(I,C);
      const __mac = P && t.macSalt ? (e => ''' + SALTED_MAC + ''')(P) : P;
      A=await (0,v.onvifResolveAdoptionCamera)(r,t.macSalt ? void 0 : A,__mac,T),'''
    replace("7.3 source snapshot and identity",
            'N=C.snapshotUri,P=await(0,g.getMAC)(I,C);A=await(0,v.onvifResolveAdoptionCamera)(r,A,P,T),',
            identity)
    replace("7.3 selected camera settings", '(0,y.getOnvifDefaultCameraSettings)(e.store,C,O,k,F)',
            '(0,y.getOnvifDefaultCameraSettings)(e.store,__details,O,k,F)')
    camera_info = 'rtspUrlLQ:null==D?void 0:D.uri,profileTokenLQ:null==D?void 0:D.profileToken,snapshotUrl:N'
    replace("7.3 persist selected profile override", camera_info,
            'rtspUrlLQ:D?.uri??(__override?null:void 0),profileTokenLQ:D?.profileToken??(__override?null:void 0),'
            'snapshotUrl:N,...__override?{manualProfileOverride:!0,hasAudio:!!M.hasAudio}:{}')
    replace("7.3 refresh profile capabilities after selected adoption",
            '(0,_.seedAvailableProfiles)(A,{overwriteExisting:!0}).catch',
            '__seedAdoptedProfiles(A,__selected).catch')

    sync_prologue = 't.syncOnvifCameraConfig=async(e,t,r)=>{var a,f,h,v;if(!r.success)return;'
    replace("7.3 preserve manual RTSP URLs during sync", sync_prologue,
            sync_prologue + 'if(t.thirdPartyCameraInfo.profileToken?.startsWith("manual-"))return;')
    replace("7.3 preserve selected profile snapshot during sync", 'let R=!1;(null!==(a=r.snapshotUri)',
            'let R=!1;(!S.manualProfileOverride||!S.snapshotUrl)&&(null!==(a=r.snapshotUri)')
    refresh = 't.updateOnvifCameraInfo=async(e,t)=>{i.default.ok(e.host,"Camera host is not defined")'
    replace("7.3 preserve manual RTSP camera info", refresh,
            refresh.replace('{i.default', '{if(e.thirdPartyCameraInfo.profileToken?.startsWith("manual-"))return;i.default'))
    replace("7.3 refresh audio from selected profile",
            'o=(0,c.getOnvifCameraStreams)(r),a=e.thirdPartyCameraInfo.errors',
            'o=e.thirdPartyCameraInfo.manualProfileOverride&&r.success'
            '?r.streams.filter(stream=>stream.profileToken===e.thirdPartyCameraInfo.profileToken)'
            ':(0,c.getOnvifCameraStreams)(r),a=e.thirdPartyCameraInfo.errors')
    # Discovery can refresh the RTSP URLs when the physical camera changes IP.
    rediscovery = 'const r=await(0,l.getCameraDetails)(t.hostname,e.port.toString(),e.username,e.password);if(r.success){const e=(0,u.getStreamData)(r),{hqStream:t,lqStream:o}=e.ok?e:{hqStream:null,lqStream:null};'
    replace("7.3 preserve selected profiles on IP change", rediscovery, '''const __tokens=e.manualProfileOverride
      ? [e.profileToken,e.profileTokenLQ].filter(Boolean) : [];
      const r=await (0,l.getCameraDetails)(t.hostname,e.port.toString(),e.username,e.password,
        {snapshotProfileTokens:__tokens.length ? [__tokens[0]] : []});
      if(r.success){
        const e=(0,u.getStreamData)(r);
        const {hqStream:t,lqStream:o}=__tokens.length
          ? {hqStream:r.streams.find(stream=>stream.profileToken===__tokens[0]),
             lqStream:r.streams.find(stream=>stream.profileToken===__tokens[1])}
          : e.ok?e:{hqStream:null,lqStream:null};''')
    replace("7.3 refresh selected snapshot on IP change",
            '...r.snapshotUri?{snapshotUrl:r.snapshotUri}:{}',
            '...(t?.snapshotUri??(__tokens.length?null:r.snapshotUri))'
            '?{snapshotUrl:t?.snapshotUri??r.snapshotUri}:{}')

    credentials = '.and(a.z.object({username:a.z.string(),password:a.z.string()}))'
    replace("7.3 adoption request schema", credentials,
            credentials[:-3] + ',profileTokens:a.z.array(a.z.string()).optional(),macSalt:a.z.string().optional(),'
            'manualStreams:a.z.array(a.z.object({uri:a.z.string().regex(/^rtsps?:\\/\\//i),'
            'quality:a.z.string().optional(),name:a.z.string().optional(),encoding:a.z.string().optional(),'
            'width:a.z.number().optional(),height:a.z.number().optional(),hasAudio:a.z.boolean().optional(),'
            'fps:a.z.number().optional(),bitrate:a.z.number().optional()})).optional()}))')
    for request in ('n={camera:t,username:i,password:a,user:e.user}', 'else n={host:o,username:i,password:a,user:e.user}'):
        replace("7.3 pass adoption options", request,
                request[:-1] + ',profileTokens:e.body.profileTokens,macSalt:e.body.macSalt,manualStreams:e.body.manualStreams}')

    # Match the stock adoption route's permission checks and request validation.
    permissions = '''(0,_.validateUserPermission)(c.AccessMode.CREATE,"camera"),
      (0,E.validateGranularUserPermission)(d.GranularAccess.SETTINGS_EDIT,c.AccessMode.CREATE,s.NvrDecalModel.modelKey,n.CameraDecalModel.modelKey),'''
    routes = '''T.route({
      method:v.MethodNames.POST,path:"/third-party-cameras/probe",
      description:"Probe an ONVIF camera for available streams",summary:"Probe ONVIF streams",tags:A,
      schema:{requestBody:{content:{[h.ContentTypes.JSON]:{schema:I}}},
        responses:(0,g.defaultJsonResponse)({[y.StatusCodes.OK]:{description:"Probe result"},
          [y.StatusCodes.BAD_REQUEST]:{description:"Unsupported camera"}})},
      handlers:[''' + permissions + '''(0,m.createPayloadValidator)("body",I),async function(req,res) {
        const {id,host,username,password}=req.body;
        const camera=id ? this.store.findByPk("camera",id) : null;
        if(id&&!camera) throw new u.NotFoundError({info:{entity:"camera",id}});
        const result=await (0,l.publish)("thirdPartyCameras.probe",{...(camera?{camera}:{host}),username,password,user:req.user});
        result?res.json(result):res.status(400).end();
      }]
    });
    const __onvifHelperHtml=''' + json.dumps(HTML) + ''';
    T.route({
      method:v.MethodNames.GET,path:"/third-party-cameras/onvif-helper",
      description:"ONVIF onboarding helper UI",summary:"ONVIF onboarding helper UI",tags:A,
      schema:{responses:(0,g.defaultJsonResponse)({[y.StatusCodes.OK]:{description:"HTML page"}})},
      handlers:[''' + permissions + '''async function(req,res) {
        res.setHeader("Content-Type","text/html; charset=utf-8");
        res.status(200).end(__onvifHelperHtml);
      }]
    });
    '''
    replace("7.3 register helper and probe routes", 't.json(a.apiSerialize())}]}),t.thirdPartyCamerasRouter=T}',
            't.json(a.apiSerialize())}]});' + routes + 't.thirdPartyCamerasRouter=T}')

    # Only fill the missing value on the permission-filtered UI response. The
    # camera's encoder and the retention calculation retain their original values.
    bootstrap_route = 'G.route({method:N.MethodNames.GET,path:"/bootstrap"'
    replace("7.3 define measured recording bitrate", bootstrap_route, RECORDED_BITRATE + bootstrap_route)
    bootstrap_response = 'for(const e of this.store.all("ringtones"))i.ringtones.push(e.apiSerialize());t.json(i)'
    replace("7.3 show measured bitrate in camera bootstrap", bootstrap_response,
            'for(const e of this.store.all("ringtones"))i.ringtones.push(e.apiSerialize());'
            'if(!(0,B.isPeerRequest)(e))await fillMissingRecordingBitrates(this.context.db,i.cameras,this.logger);'
            't.json(i)')
    return changes
