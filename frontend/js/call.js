import { getCallerId } from "./identity.js"

const callerId = getCallerId()

const statusEl = document.getElementById("status")
const transcriptEl = document.getElementById("transcript")
const endBtn = document.getElementById("endBtn")
const talkBtn = document.getElementById("talkBtn")
const waveEl = document.getElementById("wave")

let ws = null
let mediaRecorder = null
let audioChunks = []
let isRecording = false
let audioQueue = []
let isPlayingAudio = false
let audioContext = null

// Unlock AudioContext on first user gesture — required by browsers
function unlockAudio() {
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)()
    }
    if (audioContext.state === "suspended") {
        audioContext.resume()
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

// Play audio using AudioContext — works even without prior user gesture
// once AudioContext is unlocked
async function playAudioBytes(audioBytes) {
    try {
        unlockAudio()
        const buffer = await audioContext.decodeAudioData(audioBytes.slice(0))
        const source = audioContext.createBufferSource()
        source.buffer = buffer
        source.connect(audioContext.destination)

        return new Promise((resolve) => {
            source.onended = resolve
            source.start(0)
            waveEl.classList.add("animate-pulse")
        })
    } catch (err) {
        console.error("audio play error:", err)
    } finally {
        waveEl.classList.remove("animate-pulse")
    }
}

async function playAudioQueue() {
    if (isPlayingAudio || audioQueue.length === 0) return
    isPlayingAudio = true

    while (audioQueue.length > 0) {
        const audioBytes = audioQueue.shift()
        await playAudioBytes(audioBytes)
    }

    isPlayingAudio = false
    setStatus("Listening...", "text-green-400")
}

// Detect supported mime type for MediaRecorder
function getSupportedMimeType() {
    const types = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/ogg",
        "audio/mp4",
        ""
    ]
    for (const type of types) {
        if (type === "" || MediaRecorder.isTypeSupported(type)) {
            return type
        }
    }
    return ""
}

async function startRecording() {
    if (isRecording) return
    unlockAudio()

    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                sampleRate: 16000
            }
        })

        const mimeType = getSupportedMimeType()
        const options = mimeType ? { mimeType } : {}

        mediaRecorder = new MediaRecorder(stream, options)
        audioChunks = []

        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) {
                audioChunks.push(e.data)
            }
        }

        mediaRecorder.onstop = async () => {
            if (audioChunks.length === 0 || !ws || ws.readyState !== WebSocket.OPEN) return

            const mimeUsed = mediaRecorder.mimeType || "audio/webm"
            const blob = new Blob(audioChunks, { type: mimeUsed })

            // Only send if audio is long enough (avoid empty sends)
            if (blob.size < 1000) {
                setStatus("Too short — try again", "text-yellow-400")
                setTimeout(() => setStatus("Listening...", "text-green-400"), 1500)
                return
            }

            const buffer = await blob.arrayBuffer()
            const bytes = new Uint8Array(buffer)

            // Convert to base64
            let binary = ""
            for (let i = 0; i < bytes.length; i++) {
                binary += String.fromCharCode(bytes[i])
            }
            const base64 = btoa(binary)

            ws.send(JSON.stringify({ type: "audio", data: base64 }))
            audioChunks = []
        }

        // Collect data every 250ms for smoother chunks
        mediaRecorder.start(250)
        isRecording = true

        talkBtn.classList.add("bg-red-600", "border-red-400")
        talkBtn.classList.remove("bg-indigo-600", "border-indigo-400")
        talkBtn.textContent = "Recording..."
        setStatus("Recording — release to send", "text-red-400")

    } catch (err) {
        console.error("mic error:", err)
        setStatus("Mic error: " + err.message, "text-red-400")
    }
}

function stopRecording() {
    if (!isRecording || !mediaRecorder) return

    mediaRecorder.stop()
    mediaRecorder.stream.getTracks().forEach(t => t.stop())
    mediaRecorder = null
    isRecording = false

    talkBtn.classList.remove("bg-red-600", "border-red-400")
    talkBtn.classList.add("bg-indigo-600", "border-indigo-400")
    talkBtn.textContent = "Hold to Talk"
    setStatus("Processing...", "text-yellow-400")
}

// Hold to talk — mouse and touch
talkBtn.addEventListener("mousedown", (e) => { e.preventDefault(); startRecording() })
talkBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording() }, { passive: false })
talkBtn.addEventListener("mouseup", stopRecording)
talkBtn.addEventListener("touchend", stopRecording)
talkBtn.addEventListener("mouseleave", () => { if (isRecording) stopRecording() })

// WebSocket connection
const proto = window.location.protocol === "https:" ? "wss" : "ws"
const WS_URL = `${proto}://${window.location.host}/ws/call`

function connect() {
    setStatus("Connecting...", "text-yellow-400")
    ws = new WebSocket(WS_URL)

    ws.onopen = () => {
        ws.send(JSON.stringify({ type: "start", callerId }))
    }

    ws.onmessage = async (event) => {
        let msg
        try {
            msg = JSON.parse(event.data)
        } catch {
            return
        }

        if (msg.type === "status") {
            if (msg.status === "listening") setStatus("Listening... hold button to speak", "text-green-400")
            else if (msg.status === "processing") setStatus("Processing...", "text-yellow-400")
            else if (msg.status === "speaking") setStatus("Alex is speaking...", "text-indigo-400")
        }

        else if (msg.type === "transcript") {
            addTranscript(msg.role, msg.text)
        }

        else if (msg.type === "audio") {
            // Decode base64 to ArrayBuffer
            const binary = atob(msg.data)
            const bytes = new Uint8Array(binary.length)
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i)
            }
            audioQueue.push(bytes.buffer)
            playAudioQueue()
        }

        else if (msg.type === "call_ended") {
            setStatus("Call ended — redirecting...", "text-gray-400")
            talkBtn.disabled = true
            endBtn.disabled = true
            setTimeout(() => { window.location.href = "/" }, 2000)
        }

        else if (msg.type === "error") {
            setStatus("Error: " + msg.message, "text-red-400")
        }
    }

    ws.onclose = () => {
        setStatus("Disconnected", "text-gray-500")
    }

    ws.onerror = (e) => {
        setStatus("Connection failed", "text-red-400")
        console.error("ws error:", e)
    }
}

endBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "end" }))
    }
    setTimeout(() => { window.location.href = "/" }, 500)
})

// Unlock audio on page load via any interaction
document.addEventListener("click", unlockAudio, { once: true })
document.addEventListener("touchstart", unlockAudio, { once: true })

connect()
