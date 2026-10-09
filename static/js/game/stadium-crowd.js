import * as THREE from 'three';
import { createInstancedFrustumCuller } from './frustum-culling.js';

const ATLAS_URL = '/static/textures/stadium-spectators-v2.png';
const NEUTRALS = ['#d8d3c8','#38434c','#87918c','#b4967d','#ebe4d3','#47515e','#d2bd91','#686263'];
const PALETTES = { rainbow:['#bd4444','#d58743','#c8a247','#588868','#4f78aa','#8a6d9a','#bb7896'], mono:['#59616b','#91979b','#c1c2ba','#454950'] };
const seeded = seed => { const v = Math.sin(seed * 127.1 + 311.7) * 43758.5453123; return v - Math.floor(v); };
let fallbackAtlas = null, detailedAtlas = null, atlasPromise = null;
const DETAIL_RECTS = [[115,25,269,318],[418,29,556,317],[703,27,867,315],[1022,29,1158,317],[99,336,273,610],[410,338,550,607],[704,334,872,610],[1032,336,1166,609],[90,626,290,936],[408,615,576,936],[693,620,875,937],[1000,622,1175,936],[80,950,278,1244],[399,946,561,1244],[684,952,890,1246],[994,944,1201,1245]];

// Sixteen natural-color fallback cells: eight seated people and their goal reactions.
// Only blue cloth is recolored; skin, hair, denim, and shoes retain their own colors.
function makeFallbackAtlas() {
  if (fallbackAtlas) return fallbackAtlas;
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 512;
  const ctx = canvas.getContext('2d');
  const skins = ['#d8aa84','#b77c56','#724c36','#edc3a4','#986340','#ca986c','#5e4035','#e0b296'];
  const hair = ['#2c241e','#5b3e2c','#262425','#9f7e52','#39312b','#c5b9a6','#171b1d','#734638'];
  const oval = (x,y,rx,ry,color) => { ctx.fillStyle=color; ctx.beginPath(); ctx.ellipse(x,y,rx,ry,0,0,Math.PI*2); ctx.fill(); };
  const limb = (pts,width,color) => { ctx.strokeStyle=color; ctx.lineWidth=width; ctx.lineCap='round'; ctx.lineJoin='round'; ctx.beginPath(); ctx.moveTo(...pts[0]); pts.slice(1).forEach(p=>ctx.lineTo(...p)); ctx.stroke(); };
  for (let frame=0;frame<16;frame++) {
    const person=frame%8, cheer=frame>=8, skin=skins[person], hx=64+(person%3-1)*2, hy=cheer?41:58, shoulder=cheer?60:77;
    ctx.save(); ctx.translate((frame%4)*128,Math.floor(frame/4)*128);
    if (cheer) {
      limb([[54,87],[53,102],[49,117]],10,'#313c4a'); limb([[72,87],[75,101],[79,117]],10,'#354351');
      limb([[47,shoulder+3],[34,44],[32+person%4,23]],7,skin); limb([[81,shoulder+3],[94,43],[96-person%3,21]],7,skin);
      oval(33+person%4,20,4,5,skin); oval(96-person%3,18,4,5,skin);
    } else {
      limb([[52,96],[45,103],[49,119]],10,'#313c4a'); limb([[75,96],[82,103],[79,119]],10,'#354351');
      limb([[48,shoulder+3],[43,94],[56,101]],7,skin); limb([[79,shoulder+3],[84,95],[73,100]],7,skin);
    }
    oval(47,120,8,3,'#272c31'); oval(80,120,8,3,'#24282c');
    const shirt=ctx.createLinearGradient(45,0,83,0); shirt.addColorStop(0,'#214681'); shirt.addColorStop(.4,'#376bbb'); shirt.addColorStop(1,'#203f74');
    ctx.fillStyle=shirt; ctx.beginPath(); ctx.moveTo(48,shoulder-3); ctx.quadraticCurveTo(64,shoulder-9,80,shoulder-3); ctx.lineTo(79,96); ctx.quadraticCurveTo(64,101,48,96); ctx.closePath(); ctx.fill();
    limb([[49,shoulder+1],[45,shoulder+9]],10,'#2c5a9f'); limb([[79,shoulder+1],[83,shoulder+9]],10,'#2a5596');
    ctx.fillStyle=skin; ctx.fillRect(hx-4,hy+9,8,8);
    if(person%3===1) oval(hx,hy+5,12,18,hair[person]);
    oval(hx,hy,10,13,skin); oval(hx-9,hy+1,2,4,skin); oval(hx+9,hy+1,2,4,skin);
    ctx.fillStyle=hair[person]; ctx.beginPath(); ctx.ellipse(hx,hy-5,11,10,0,Math.PI,Math.PI*2); ctx.fill();
    if(person===2||person===6) oval(hx,hy+8,7,4,hair[person]);
    oval(hx-3.5,hy,1,.9,'#352d28'); oval(hx+3.5,hy,1,.9,'#352d28'); limb([[hx-2,hy+6],[hx+2,hy+6]],.8,'#855c49');
    if(person===3) limb([[hx-8,hy],[hx+8,hy]],1,'#4e4137');
    ctx.strokeStyle='#a3b9d5'; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(57,shoulder-4); ctx.quadraticCurveTo(64,shoulder+3,71,shoulder-4); ctx.stroke();
    if(person%2) {ctx.fillStyle='#afc4df';ctx.fillRect(70,shoulder+7,3,3);} ctx.restore();
  }
  fallbackAtlas=new THREE.CanvasTexture(canvas); fallbackAtlas.colorSpace=THREE.SRGBColorSpace;
  fallbackAtlas.minFilter=THREE.LinearFilter; fallbackAtlas.magFilter=THREE.LinearFilter; fallbackAtlas.generateMipmaps=false;
  return fallbackAtlas;
}
function loadDetailedAtlas() {
  if(detailedAtlas) return Promise.resolve(detailedAtlas);
  if(!atlasPromise) atlasPromise=new Promise(resolve=>new THREE.TextureLoader().load(ATLAS_URL,texture=>{
    texture.colorSpace=THREE.SRGBColorSpace; texture.minFilter=THREE.LinearMipmapLinearFilter; texture.magFilter=THREE.LinearFilter; texture.anisotropy=4;
    detailedAtlas=texture; resolve(texture);
  },undefined,()=>resolve(null)));
  return atlasPromise;
}

// One depth-tested cutout quad per person. No fan geometry or CPU animation loop.
export function createCrowdSprites(positions,options={}) {
  const geometry=new THREE.PlaneGeometry(26,38); geometry.translate(0,19,0);
  // Billboards can tilt at an overhead view, so bounds cover all poses about feet.
  geometry.boundingSphere=new THREE.Sphere(new THREE.Vector3(),43);
  const spriteIndices=new Float32Array(positions.length), bobOffsets=new Float32Array(positions.length), sections=new Float32Array(positions.length);
  const uniforms={
    uSpriteSheet:{value:makeFallbackAtlas()},uAtlasInset:{value:.005},
    uAtlasRects:{value:Array.from({length:16},(_,i)=>new THREE.Vector4(i%4/4,(3-Math.floor(i/4))/4,(i%4+1)/4,(4-Math.floor(i/4))/4))},
    uPoseSize:{value:Array.from({length:16},()=>new THREE.Vector2(26,38))},
    uTime:{value:0},uCheerPhase:{value:0},uCheerStart:{value:0},uWavePhase:{value:0},
    uCamRight:{value:new THREE.Vector3(1,0,0)},uCamUp:{value:new THREE.Vector3(0,1,0)},
    uTeamA:{value:new THREE.Color('#3b82f6')},uTeamB:{value:new THREE.Color('#ef4444')},
    uPalette:{value:NEUTRALS.map(c=>new THREE.Color(c))},uPaletteCount:{value:8},uPaletteMode:{value:0},
  };
  const material=new THREE.ShaderMaterial({uniforms,transparent:false,depthWrite:true,side:THREE.DoubleSide,
    vertexShader:`
      attribute float aSpriteIndex; attribute float aBobOffset; attribute float aSection;
      uniform float uTime; uniform float uCheerPhase; uniform float uCheerStart; uniform float uWavePhase;
      uniform vec3 uCamRight; uniform vec3 uCamUp; uniform vec3 uTeamA; uniform vec3 uTeamB;
      uniform vec3 uPalette[8]; uniform float uPaletteCount; uniform float uPaletteMode;
      uniform vec2 uPoseSize[16];
      varying vec2 vUv; varying vec3 vColor; varying float vFrame;
      void main() {
        vec4 wp=modelMatrix*instanceMatrix*vec4(0.0,0.0,0.0,1.0);
        float sx=length(vec3(instanceMatrix[0])), sy=length(vec3(instanceMatrix[1]));
        float variety=fract(aBobOffset*7.17);
        int clothing=int(floor(fract(aBobOffset*2.31)*uPaletteCount));
        vec3 shirt=mix(uTeamB,uTeamA,aSection);
        if(uPaletteMode>2.5) shirt=uTeamB;
        else if(uPaletteMode>1.5) shirt=uTeamA;
        else if(uPaletteMode>.5 || variety>.67) shirt=uPalette[clothing];
        vColor=shirt*(.82+fract(aBobOffset*4.13)*.22);
        float age=uTime-uCheerStart-fract(aBobOffset*1.47)*.72;
        float reaction=uCheerPhase*smoothstep(.0,.24,age)*(1.0-smoothstep(3.55,4.05,age));
        reaction*=step(.16,fract(aBobOffset*3.13));
        vFrame=aSpriteIndex+8.0*step(.32,reaction);
        vUv=vec2(mix(uv.x,1.0-uv.x,step(.5,fract(aBobOffset*5.91))),uv.y);
        wp.y+=max(0.0,sin(age*(4.1+variety)+aBobOffset))*reaction*.9;
        vec3 right=vec3(uCamRight.x,0.0,uCamRight.z);
        if(length(right)<.01) right=vec3(1.0,0.0,0.0);
        right=normalize(right);
        // Upright in broadcast views, readable silhouettes in tactical overhead views.
        float overhead=(1.0-smoothstep(.08,.4,abs(uCamUp.y)))*.7;
        vec3 up=normalize(mix(vec3(0.0,1.0,0.0),uCamUp,overhead));
        vec2 size=uPoseSize[int(floor(vFrame+.5))];
        vec3 point=wp.xyz+right*(position.x/26.0)*size.x*sx+up*(position.y/38.0)*size.y*sy;
        point+=right*sin(uTime*.85+aBobOffset)*.06;
        gl_Position=projectionMatrix*viewMatrix*vec4(point,1.0);
      }`,
    fragmentShader:`
      uniform sampler2D uSpriteSheet; uniform float uAtlasInset; uniform vec4 uAtlasRects[16];
      varying vec2 vUv; varying vec3 vColor; varying float vFrame;
      void main() {
        vec4 crop=uAtlasRects[int(floor(vFrame+.5))];
        vec2 safeUv=mix(vec2(uAtlasInset),vec2(1.0-uAtlasInset),vUv);
        vec4 sprite=texture2D(uSpriteSheet,mix(crop.xy,crop.zw,safeUv));
        if(sprite.a<.42) discard;
        float mask=smoothstep(.018,.10,sprite.b-max(sprite.r,sprite.g));
        float shade=clamp(sprite.b*1.65,.12,1.3);
        gl_FragColor=vec4(mix(sprite.rgb,vColor*shade,mask),1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  const mesh=new THREE.InstancedMesh(geometry,material,positions.length); mesh.name='stadium-spectators';
  const dummy=new THREE.Object3D();
  positions.forEach(([x,y,z],index)=>{
    const scale=.9+seeded(index+7)*.18;
    dummy.position.set(x+(seeded(index+137)-.5)*1.2,y,z); dummy.scale.set(scale*(.92+seeded(index+89)*.14),scale,1); dummy.updateMatrix(); mesh.setMatrixAt(index,dummy.matrix);
    spriteIndices[index]=Math.floor(seeded(index+38)*8); bobOffsets[index]=seeded(index+72)*Math.PI*2; sections[index]=z>0?1:0;
  });
  geometry.setAttribute('aSpriteIndex',new THREE.InstancedBufferAttribute(spriteIndices,1));
  geometry.setAttribute('aBobOffset',new THREE.InstancedBufferAttribute(bobOffsets,1));
  geometry.setAttribute('aSection',new THREE.InstancedBufferAttribute(sections,1)); mesh.instanceMatrix.needsUpdate=true;
  const culler=createInstancedFrustumCuller(THREE,mesh,{padding:2}); let atlasLoaded=false;
  function setPalette(settings={}) {
    uniforms.uTeamA.value.set(settings.teamA||'#3b82f6'); uniforms.uTeamB.value.set(settings.teamB||'#ef4444');
    const name=settings.paletteName||'classic', chosen=settings.palette||PALETTES[name]||NEUTRALS, palette=chosen.length?chosen:NEUTRALS;
    uniforms.uPalette.value.forEach((c,i)=>c.set(palette[i%palette.length])); uniforms.uPaletteCount.value=Math.min(8,palette.length);
    uniforms.uPaletteMode.value=name==='team_b'?3:name==='team_a'?2:name==='classic'?0:1; options.invalidate?.();
  }
  setPalette(options);
  if(options.detailed!==false) loadDetailedAtlas().then(texture=>{
    if(!texture) return;
    uniforms.uSpriteSheet.value=texture; uniforms.uAtlasInset.value=.002;
    const width=texture.image.width, height=texture.image.height;
    DETAIL_RECTS.forEach(([left,top,right,bottom],index)=>{
      left=Math.max(0,left-3); top=Math.max(0,top-3); right=Math.min(width,right+3); bottom=Math.min(height,bottom+3);
      uniforms.uAtlasRects.value[index].set(left/width,1-bottom/height,right/width,1-top/height);
      const poseHeight=index<8?25:36;
      uniforms.uPoseSize.value[index].set((right-left)/(bottom-top)*poseHeight,poseHeight);
    });
    atlasLoaded=true; options.invalidate?.();
  });
  return {mesh,uniforms,culler,setPalette,stats:()=>({...culler.stats(),atlasLoaded,atlas:atlasLoaded?'detailed':'procedural',atlasResolution:[uniforms.uSpriteSheet.value.image.width,uniforms.uSpriteSheet.value.image.height],variants:8,poses:2,drawCalls:1,paletteMode:uniforms.uPaletteMode.value})};
}

// Deliberate aisle gaps and occasional empty seats break up the repeated grid.
export function audienceLayout(width,height) {
  const seats=[],positions=[],tiers=[];
  for(let side=0;side<4;side++) for(let row=0;row<8;row++) {
    const span=(side<2?width:height)/2-32, columns=Math.floor(span*2/14)+1, start=-(columns-1)*7;
    const outward=(side<2?height/2:width/2)+34+row*16, floor=4+row*11, direction=side%2?-1:1;
    tiers.push(side<2?[span*2+20+row*6,8,18,0,floor-4,outward*direction]:[18,8,span*2+20+row*6,outward*direction,floor-4,0]);
    for(let column=0;column<columns;column++) {
      if(column%20===9||column%20===10) continue;
      const along=start+column*14, position=side<2?[along,floor-.1,outward*direction]:[outward*direction,floor-.1,along];
      seats.push({position,side,row}); if(seeded(side*1909+row*157+column+13)>.045) positions.push(position);
    }
  }
  return {positions,seats,tiers};
}
export function createStadiumAudience(width,height,options={}) {
  const {positions,seats,tiers}=audienceLayout(width,height), group=new THREE.Group(); group.name='stadium-audience';
  const box=new THREE.BoxGeometry(1,1,1), material=new THREE.MeshStandardMaterial({color:options.seatColor||'#475569',roughness:.93}), dummy=new THREE.Object3D();
  function boxes(items,name,shade) {
    const mesh=new THREE.InstancedMesh(box,material,items.length); mesh.name=name;
    items.forEach(([w,h,d,x,y,z],index)=>{dummy.position.set(x,y,z);dummy.scale.set(w,h,d);dummy.rotation.set(0,0,0);dummy.updateMatrix();mesh.setMatrixAt(index,dummy.matrix);mesh.setColorAt(index,new THREE.Color().setScalar(shade(index)));});
    mesh.instanceMatrix.needsUpdate=true;mesh.userData.frustumCuller=createInstancedFrustumCuller(THREE,mesh);group.add(mesh);return mesh;
  }
  // Short sections prevent a long tier's conservative sphere from covering a
  // camera aimed completely away from the stadium. They share one draw/buffer.
  function sections(items) {
    return items.flatMap(([w,h,d,x,y,z])=>{
      const alongX=w>d, span=alongX?w:d, count=Math.ceil(span/64), length=span/count;
      return Array.from({length:count},(_,index)=>{
        const offset=(index-(count-1)/2)*length;
        return alongX?[length,h,d,x+offset,y,z]:[w,h,length,x,y,z+offset];
      });
    });
  }
  const tierSections=sections(tiers);
  const tierMesh=boxes(tierSections,'stadium-tiers',i=>Math.round(tierSections[i][4]/11)%2?.58:.7);
  // Seat backs are thin surfaces, so two triangles suffice instead of a box's twelve.
  const seatMaterial=material.clone(); seatMaterial.side=THREE.DoubleSide;
  const seatMesh=new THREE.InstancedMesh(new THREE.PlaneGeometry(9,5),seatMaterial,seats.length);
  seatMesh.name='stadium-seat-backs';
  seats.forEach(({position:[x,y,z],side},index)=>{
    const direction=side%2?-1:1;
    dummy.position.set(x+(side>=2?direction*4:0),y+8,z+(side<2?direction*4:0));
    dummy.scale.set(1,1,1);
    dummy.rotation.set(0,side<2?side*Math.PI:(side===2?Math.PI/2:-Math.PI/2),0);
    dummy.updateMatrix(); seatMesh.setMatrixAt(index,dummy.matrix);
    seatMesh.setColorAt(index,new THREE.Color().setScalar(.86+seeded(index+29)*.2));
  });
  seatMesh.instanceMatrix.needsUpdate=true;
  seatMesh.userData.frustumCuller=createInstancedFrustumCuller(THREE,seatMesh);
  group.add(seatMesh);
  const walls=[[width+240,20,18,0,10,height/2+170],[width+240,20,18,0,10,-height/2-170],[18,20,height+260,width/2+150,10,0],[18,20,height+260,-width/2-150,10,0]];
  const wallMesh=boxes(sections(walls),'stadium-outer-walls',()=>.4), crowd=createCrowdSprites(positions,options);group.add(crowd.mesh);const seating=[tierMesh,seatMesh,wallMesh];
  return {group,...crowd,seating,update(camera){crowd.culler.update(camera);seating.forEach(mesh=>mesh.userData.frustumCuller.update(camera));},setSeatColor(hex){material.color.set(hex);seatMaterial.color.set(hex);options.invalidate?.();}};
}
