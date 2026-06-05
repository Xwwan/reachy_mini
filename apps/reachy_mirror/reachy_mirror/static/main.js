// ** INITIAL VARS
let appState
let stream
let frameRateUp = 10
let frameCount = 0
let frameCountTotal = 0
let streamInterval
let readyInterval
let ready = false
let t0 = new Date()
let t0Total = new Date()

// ** PROCESS STATUS
async function checkStatus() {
  try {
    const resp = await fetch('/ready')
    const data = await resp.json()
    if( data.ready ) {
      status.textContent = '✅ Streaming'
      remote.style.display = ''
      spinner.style.display = 'none'
      remote.setAttribute('src', '/webcam_feed')
      await startWebcam()
      await setAppState()
      setStreamInterval()
      setReadyInterval()
    }
    else {
      status.textContent = '⏳ Initializing...'
      processedWrapper.style.display = 'none'
      spinner.style.display = 'block'
    }
  } catch( e ) {
    status.textContent = '🔄 Connecting...'
    processedWrapper.style.display = 'none'
    remote.style.display = 'none'
    remote.setAttribute('src', '')
    spinner.style.display = 'block'
  }
}

function setReadyInterval(running) {
  if( readyInterval ) {
    clearInterval(readyInterval)
    readyInterval = null
  }
  if( running ) readyInterval = setInterval(checkStatus, 1000)
}

function setStreamInterval() {
  if( streamInterval ) clearInterval(streamInterval)
  streamInterval = setInterval(sendFrameToBackend, 1000/appState.frameRateUp)
}

async function startWebcam() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } } })
    webcam.srcObject = stream
    frameCount = 0
    frameCountTotal = 0
    t0 = new Date()
    t0Total = new Date()
  } catch (err) {
    console.error('Erreur video:', err)
  }
}

function stopWebcam() {
  if( stream ) {
    stream.getTracks().forEach(track => track.stop())
    webcam.srcObject = null
    stream = null
  }
    
  if( streamInterval ) {
    clearInterval(streamInterval)
    streamInterval = null
  }
    
  processed.src = ''
  processedHead.textContent = ''
  processedHands.textContent = ''
  processedWrapper.style.display = 'none'
}

// ** HTML ELEMENT
const status = document.getElementById('status')
const spinner = document.getElementById('spinner')
const webcam = document.getElementById('webcam')
const remote = document.getElementById('remote')
const processed = document.getElementById('processed')
const processedWrapper = document.getElementById('processed-wrapper')
const processedHead = document.getElementById('processed-head')
const processedHands = document.getElementById('processed-hands')
const frameRateUpValue = document.getElementById('frameRateUpValue')
const frameRateDownValue = document.getElementById('frameRateDownValue')
const motionReductionValue = document.getElementById('motionReductionValue')
const showProcessingToggle = document.getElementById('showProcessingToggle')
const isMirrorToggle = document.getElementById('isMirrorToggle')

// * FPS UP
document.getElementById('frameRateUpSlider').addEventListener('input', function() {
  document.getElementById('frameRateUpValue').textContent = parseInt(this.value)
})
document.getElementById('frameRateUpSlider').addEventListener('change', function() {
  const value = parseInt(this.value)
  appState.frameRateUp = value
  setCookie('frameRateUp', value)
  setStreamInterval()
})

// * FPS DOWN
document.getElementById('frameRateDownSlider').addEventListener('input', function() {
  document.getElementById('frameRateDownValue').textContent = parseInt(this.value)
})
document.getElementById('frameRateDownSlider').addEventListener('change', async function() {
  const value = parseInt(this.value)
  appState.frameRateDown = value
  setCookie('frameRateDown', value)
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ frameRateDown: value })
  })
})

// * MOTION REDUCTION
document.getElementById('motionReductionSlider').addEventListener('input', function() {
  document.getElementById('motionReductionValue').textContent = parseInt(this.value)
})
document.getElementById('motionReductionSlider').addEventListener('change', async function() {
  const value = parseInt(this.value)
  appState.motionReduction = value
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ motionReduction: value })
  })
  setCookie('motionReduction', value)
})

// * PROCESSING DISPLAY
document.getElementById('showProcessingToggle').addEventListener('change', async function() {
  appState.showProcessing = this.checked
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ showProcessing: this.checked })
  })
  setCookie('showProcessing', this.checked ? 'true' : 'false')
  processedWrapper.style.display = this.checked ? 'block' : 'none'
})

// * MIRROR MODE
document.getElementById('isMirrorToggle').addEventListener('change', async function() {
  appState.isMirror = this.checked
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isMirror: this.checked })
  })
  setCookie('isMirror', this.checked ? 'true' : 'false')
})

document.getElementById('resetButton').addEventListener('click', async function() {
  reset()
  setAppState()
})


// ** COOKIE MANAGEMENT
function setCookie(key, value) {
    const expires = new Date()
    expires.setTime(expires.getTime() + (1 * 24 * 60 * 60 * 1000))
    document.cookie = key + '=' + value + ';expires=' + expires.toUTCString()
}

function getCookie(key) {
    const keyValue = document.cookie.match('(^|;) ?' + key + '=([^;]*)(;|$)')
    return keyValue ? keyValue[2] : null
}

function deleteCookie(name) {
  document.cookie = name+'=; Max-Age=-99999999;';
}

// ** BACKEND HANDLER
async function sendFrameToBackend() {
  if (!webcam || webcam.paused || webcam.ended) return
  
  const canvas = document.createElement('canvas')
  canvas.width = webcam.videoWidth
  canvas.height = webcam.videoHeight
  const ctx = canvas.getContext('2d')
  ctx.drawImage(webcam, 0, 0)
  const imageData = canvas.toDataURL('image/jpeg', 0.8)

  try {
    const resp = await fetch('/process_frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: imageData })
    })
    if( appState.showProcessing ) {
      const data = await resp.json()
      if( data.image ) {
        processed.src = data.image
        frameCount++
        frameCountTotal++
        const now = new Date()
        if( now - t0 > 1000 ) { // 1 second
          status.textContent = `✅ Streaming `
          status.textContent += `• UP: ${Math.floor(frameCount/(now - t0)*10000)/10} FPS (~avg ${Math.floor(frameCountTotal/(now - t0Total)*10000)/10})`
          if( data.downValue && data.downValueAvg ) status.textContent += ` • DOWN: ${data.downValue} FPS (~avg ${data.downValueAvg})`
          t0 = now
          frameCount = 0
        }
        if( data.head ) processedHead.textContent = `pitch: ${data.head[0]}\nyaw: ${data.head[1]}\nroll: ${data.head[2]}`
        if( data.hands ) processedHands.textContent = `left: ${data.hands[0]}\nright: ${data.hands[1]}`
        processedWrapper.style.display = 'block'
      }
      else if( data.error ) console.error('Erreur backend:', data.error)
    }
  }
  catch(e) {
    console.error('Erreur réseau:', e)
    stopWebcam()
    setReadyInterval(true)
  }
}

function reset() {
  deleteCookie('frameRateUp')
  deleteCookie('frameRateDown')
  deleteCookie('motionReduction')
  deleteCookie('showProcessing')
  deleteCookie('isMirror')
}

async function setAppState() {
  appState = {}
  appState.frameRateUp = frameRateUpSlider.value = frameRateUpValue.textContent = getCookie('frameRateUp') ?? 30, // 30 FPS UP
  appState.frameRateDown = frameRateDownSlider.value = frameRateDownValue.textContent = getCookie('frameRateDown') ?? 60, // 60 FPS DOWN
  appState.motionReduction = motionReductionSlider.value = motionReductionValue.textContent = getCookie('motionReduction') ?? 60, // 60% by default
  appState.showProcessing = showProcessingToggle.checked = getCookie('showProcessing') === null ? true : getCookie('showProcessing') === 'true' ? true : false // Show processed
  processedWrapper.style.display = 'none'
  appState.isMirror = isMirrorToggle.checked = getCookie('isMirror') === null ? true : getCookie('isMirror') === 'true' ? true : false // Mirror mode

  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      frameRateDown: appState.frameRateDown,
      motionReduction: appState.motionReduction,
      showProcessing: appState.showProcessing,
      isMirror: appState.isMirror
    })
  })
}

window.addEventListener('beforeunload', _ => {
  stopWebcam()
  setReadyInterval()
})

setReadyInterval(true)