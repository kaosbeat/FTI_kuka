let vid;
let vid1s = 0
let vid1d = 40
let vid2s = 3
let vid2d = 10
let vids = [[0,40],[3,10],[6,80]]
let playing = false;
let completion;
let idx=0

let libs = ['https://unpkg.com/hydra-synth', 'includes/libs/hydra-synth.js',  "https://unpkg.com/ml5@1/dist/ml5.js"]

let handPose;
let hands = [];

let hc = document.createElement('canvas')
// hc.width = window.innerWidth
// hc.height = window.innerHeight
hc.width=1080
hc.height=1080
document.body.appendChild(hc)
let hydra = new Hydra({ detectAudio: true, canvas: hc })
// hydra.setResolution(window.innerWidth*2, window.innerHeight*2) // retina res
noize = noise // use noize() since noise() is taken by p5js
a.show()

let pg // store hydra texture
// sandbox - start
// a.show()
src(o0)
	.layer(src(s0))
	// .scale(()=>a.fft[0]*0+.996)
	.modulateScale(src(s0).pixelate(()=>Math.round((a.fft[2]*15))+1, 10), .5)
	.scrollY(1.003)
	// .rotate( ()=>sin(frameCount*.01)*.1 ) // use p5 vars!
	// .modulateScale(osc(5, .1), .56)
	// .blend(src(o0).scale(0.8))
	.out(o0)	
// sandbox - stop


function preload() {
  handPose = ml5.handPose()
    vid = createVideo("img/allhands.mp4", VIDEO);
}


			            
			          


function setup() {
  createCanvas(1080, 1080, WEBGL);
  setupMidi(0, 0)
  
  pg = createGraphics(hc.width, hc.height)
  s0.init({src: drawingContext.canvas })
  //midi5.debug = ['note', 'controlchange'] 

  //vid = createVideo("img/hand.mp4");
  vid.size(1080, 1080);
  vid.hide()
  vid.play();
  vid.volume(0);
  //handPose.detectStart(vid, gotHands);
  
  
  	// set custom functions
	midi5.noteOn = (note) => {
		print(note)
		// fill(note.velocity)
		// noStroke()
		// circle(random(width), random(height), note.number * note.octave)
		if (note.type=="noteon"){
			if (note.number == 47) {vid.play(); vid.time(0); }
			if (note.number == 48) {vid.play(); vid.time(2); }
			if (note.number == 49) {vid.play(); vid.time(5 ); }
		} 
		// 
	}
	midi5.noteOff = (note) => {
			vid.pause(); 
		}
	
}

function gotHands(results) {
  // Save the output to the hands variable
  hands = results;
}


function draw() {
	updateMidi()
	pg.drawingContext.drawImage(hc, 0, 0, pg.width, pg.height)
  //background(vid.volume()*255);
  console.log()
  //if (frameCount % 40 == 0) {
  //   vid.pause();
  //}
 // if (frameCount % vids[idx][1] == 0) {
 // 	frameCount = 0
	// vid.time(vids[idx][0]);
	
 // }
  //ellipse((vid.time()/vid.duration())*width,
  //  50, 20, 20);
   push()
   scale(1)
  translate(-540,-540)
  image(vid,0,0)
   pop()
   
  // Draw all the tracked hand points
  for (let i = 0; i < hands.length; i++) {
    let hand = hands[i];
    for (let j = 0; j < hand.keypoints.length; j++) {
      let keypoint = hand.keypoints[j];
      fill(0, 255, 0);
      noStroke();
      circle(keypoint.x, keypoint.y, 10);
    }
  }

}
