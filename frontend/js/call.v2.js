import { getCallerId } from "./identity.js"

const callerId = getCallerId()

const statusEl = document.getElementById("status")
const transcriptEl = document.getElementById("transcript")
const endBtn = document.getElementById("endBtn")
const micBtn = document.getElementById("micBtn")
const waveEl = document.getElementById("wave")
const unlockHint = document.getElementById("unlockHint")

// ---- VAD tuning constants ----
// If the mic barely ever triggers recording, LOWER SPEECH_THRESHOLD.
// If background noise keeps triggering false recordings, RAISE it.
const SPEECH_THRESHOLD = 0.035        // RMS volume (0-1) that counts as "speaking"
const SILENCE_DURATION_MS = 900       // how long silence must persist after speech to auto-send
const MIN_SPEECH_DURATION_MS = 300    // ignore blips shorter than this
const MAX_RECORDING_MS = 20000        // safety cap so a stuck VAD can't record forever

let ws = null
let mediaRecorder = null
let audioChunks = []
let isRecording = false
let audioQueue = []
let isPlayingAudio = false
let audioContext = null
let audioUnlocked = false

let micStream = null
let analyser = null
let dataArray = null
let vadActive = false          // true only during the user's turn, after agent audio finished
let vadLoopRunning = false
let speechStartTime = null
let lastVoiceTime = null

function unlockAudio() {
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)()
    }
    if (audioContext.state === "suspended") audioContext.resume()
    if (!audioUnlocked) {
        audioUnlocked = true
        if (unlockHint) unlockHint.classList.add("hidden")
        setupAnalyserIfReady()
        startVadLoopIfReady()
    }
}

function setStatus(text, color = "text-gray-400") {
    statusEl.textContent = text
    statusEl.className = `text-sm font-medium ${color}`
}

function addTranscript(role, text) {
    const div = document.createElement("div")
    div.className = role === "agent"
        ? "flex gap-3 items-start"
        : "flex gap-3 items-start flex-row-reverse"
    const bubble = document.createElement("div")
    bubble.className = role === "agent"
        ? "bg-gray-700 text-white px-4 py-2 rounded-2xl rounded-tl-none max-w-xs text-sm leading-relaxed"
        : "bg-indigo-600 text-white px-4 py-2 rounded-2xl rounded-tr-none max-w-xs text-sm leading-relaxed"
    bubble.textContent = text
    const label = document.createElement("span")
    label.className = "text-xs text-gray-500 mt-2 shrink-0"
    label.textContent = role === "agent" ? "Alex" : "You"
    div.appendChild(label)
    div.appendChild(bubble)
    transcriptEl.appendChild(div)
    transcriptEl.scrollTop = transcriptEl.scrollHeight
}

async function playAudioBytes(arrayBuffer) {
    try {
        unlockAudio()
        const buffer = await audioContext.decodeAudioData(arrayBuffer)
        const source = audioContext.createBufferSource()
        source.buffer = buffer
        source.connect(audioContext.destination)
        waveEl.classList.add("animate-pulse")
        return new Promise((resolve) => {
            source.onended = () => {
                waveEl.classList.remove("animate-pulse")
                resolve()
            }
            source.start(0)
        })
    } catch (err) {
        console.error("audio play error:", err)
        waveEl.classList.remove("animate-pulse")
    }
}

async function playAudioQueue() {
    if (isPlayingAudio || audioQueue.length === 0) return
    isPlayingAudio = true
    disableVad() // never listen while Alex's audio might still be playing
    while (audioQueue.length > 0) {
        await playAudioBytes(audioQueue.shift())
    }
    isPlayingAudio = false
    setStatus("Listening — just speak", "text-green-400")
    enableVad() // now it's genuinely the user's turn
}

function getSupportedMimeType() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", ""]
    for (const t of types) {
        if (!t || MediaRecorder.isTypeSupported(t)) return t
    }
    return ""
}

function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result.split(",")[1])
        reader.onerror = reject
        reader.readAsDataURL(blob)
    })
}

// ---- Persistent mic setup (requested once, reused all call) ----

async function initMic() {
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
        setupAnalyserIfReady()
        startVadLoopIfReady()
    } catch (err) {
        console.error("mic error:", err)
        setStatus("Mic access denied — allow microphone access and refresh", "text-red-400")
    }
}

function setupAnalyserIfReady() {
    if (!micStream || !audioContext || analyser) return
    const source = audioContext.createMediaStreamSource(micStream)
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)
    dataArray = new Uint8Array(analyser.frequencyBinCount)
}

function getVolume() {
    if (!analyser || !dataArray) return 0
    analyser.getByteTimeDomainData(dataArray)
    let sum = 0
    for (let i = 0; i < dataArray.length; i++) {
        const v = (dataArray[i] - 128) / 128
        sum += v * v
    }
    return Math.sqrt(sum / dataArray.length)
}

function updateWaveVisual(volume) {
    const bars = waveEl.children
    const scale = Math.min(1, volume / 0.15) // normalize for visual scaling
    for (let i = 0; i < bars.length; i++) {
        const wobble = 0.4 + Math.random() * 0.6
        const heightPx = 6 + scale * wobble * 34
        bars[i].style.height = `${heightPx}px`
    }
}

function enableVad() {
    vadActive = true
    speechStartTime = null
    lastVoiceTime = null
    if (micBtn) {
        micBtn.textContent = "Send Now"
        micBtn.disabled = false
    }
}

function disableVad() {
    vadActive = false
    speechStartTime = null
    lastVoiceTime = null
}

function startVadLoopIfReady() {
    if (vadLoopRunning || !analyser || !audioContext) return
    vadLoopRunning = true
    requestAnimationFrame(vadLoop)
}

function vadLoop() {
    const volume = getVolume()
    updateWaveVisual(volume)
    const now = performance.now()

    if (vadActive) {
        if (volume > SPEECH_THRESHOLD) {
            if (!isRecording) {
                startRecording()
                speechStartTime = now
            }
            lastVoiceTime = now
        } else if (isRecording && lastVoiceTime !== null) {
            const silenceElapsed = now - lastVoiceTime
            const speechElapsed = speechStartTime !== null ? now - speechStartTime : 0
            if (silenceElapsed > SILENCE_DURATION_MS && speechElapsed > MIN_SPEECH_DURATION_MS) {
                stopRecording()
            }
        }

        if (isRecording && speechStartTime !== null && (now - speechStartTime) > MAX_RECORDING_MS) {
            console.warn("VAD safety cap hit — forcing send")
            stopRecording()
        }
    }

    requestAnimationFrame(vadLoop)
}

// ---- Recording (uses the persistent micStream, not a fresh getUserMedia call) ----

function startRecording() {
    if (isRecording || !micStream) return
    unlockAudio()

    const mimeType = getSupportedMimeType()
    const options = mimeType ? { mimeType } : {}

    const recorder = new MediaRecorder(micStream, options)
    mediaRecorder = recorder
    audioChunks = []

    recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) audioChunks.push(e.data)
    }

    recorder.onstop = async () => {
        // NOTE: unlike before, we do NOT stop micStream tracks here —
        // the stream is reused for the whole call, only this recorder ends.
        const chunks = [...audioChunks]
        audioChunks = []

        if (chunks.length === 0) {
            console.warn("no audio chunks recorded")
            return
        }

        const mimeUsed = recorder.mimeType || "audio/webm"
        const blob = new Blob(chunks, { type: mimeUsed })
        console.log(`audio blob: ${blob.size} bytes, type: ${mimeUsed}`)

        if (blob.size < 500) {
            console.warn("recording too short, discarding")
            return
        }

        try {
            const base64 = await blobToBase64(blob)
            console.log(`sending audio: ${base64.length} base64 chars`)
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "audio", data: base64 }))
            } else {
                console.error("WebSocket not open when trying to send audio")
                setStatus("Disconnected — refresh page", "text-red-400")
            }
        } catch (err) {
            console.error("base64 error:", err)
        }
    }

    recorder.start(250)
    isRecording = true
    setStatus("Listening to you...", "text-red-400")
    if (micBtn) micBtn.textContent = "Send Now"
}

function stopRecording() {
    if (!isRecording || !mediaRecorder) return
    mediaRecorder.stop()
    isRecording = false
    disableVad() // it's no longer the user's turn until the agent responds
    setStatus("Processing...", "text-yellow-400")
    if (micBtn) micBtn.disabled = true
}

// Manual override button: lets the user force-send early if VAD hasn't
// triggered a stop yet, or force-start if VAD's threshold missed their voice.
if (micBtn) {
    micBtn.addEventListener("click", () => {
        if (isRecording) {
            stopRecording()
        } else if (vadActive) {
            startRecording()
            speechStartTime = performance.now()
            lastVoiceTime = performance.now()
        }
    })
}

const proto = window.location.protocol === "https:" ? "wss" : "ws"
const WS_URL = `${proto}://${window.location.host}/ws/call`

function connect() {
    setStatus("Connecting...", "text-yellow-400")
    ws = new WebSocket(WS_URL)

    ws.onopen = () => {
        console.log("WebSocket connected")
        ws.send(JSON.stringify({ type: "start", callerId }))
    }

    ws.onmessage = async (event) => {
        let msg
        try { msg = JSON.parse(event.data) } catch { return }
        console.log("ws msg:", msg.type, msg.status || msg.role || "")

        if (msg.type === "status") {
            if (msg.status === "listening") {
                // Actual VAD arming happens after playback truly finishes
                // (see playAudioQueue) — this just updates the label if
                // there's no audio queued (e.g. retry messages).
                if (!isPlayingAudio && audioQueue.length === 0) {
                    setStatus("Listening — just speak", "text-green-400")
                    enableVad()
                }
            }
            else if (msg.status === "processing") {
                disableVad()
                setStatus("Processing...", "text-yellow-400")
            }
            else if (msg.status === "speaking") {
                disableVad()
                setStatus("Alex is speaking...", "text-indigo-400")
            }
        }
        else if (msg.type === "transcript") {
            addTranscript(msg.role, msg.text)
        }
        else if (msg.type === "audio") {
            const binary = atob(msg.data)
            const bytes = new Uint8Array(binary.length)
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
            audioQueue.push(bytes.buffer.slice(0))
            playAudioQueue()
        }
        else if (msg.type === "call_ended") {
            setStatus("Call ended — redirecting...", "text-gray-400")
            disableVad()
            if (micBtn) micBtn.disabled = true
            endBtn.disabled = true
            setTimeout(() => { window.location.href = "/" }, 2000)
        }
        else if (msg.type === "error") {
            setStatus("Error: " + msg.message, "text-red-400")
            console.error("server error:", msg.message)
        }
    }

    ws.onclose = (e) => {
        console.log("ws closed:", e.code, e.reason)
        setStatus("Disconnected", "text-gray-500")
        disableVad()
    }

    ws.onerror = (e) => {
        console.error("ws error:", e)
        setStatus("Connection error", "text-red-400")
    }
}

endBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "end" }))
    disableVad()
    if (micStream) micStream.getTracks().forEach(t => t.stop())
    setTimeout(() => { window.location.href = "/" }, 500)
})

document.addEventListener("click", unlockAudio, { once: true })
document.addEventListener("touchstart", unlockAudio, { once: true })

initMic()
connect()
