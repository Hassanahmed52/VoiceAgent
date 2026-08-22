import { getCallerId } from "./identity.js"

const callerId = getCallerId()

// UI elements
const statusEl = document.getElementById("status")
const transcriptEl = document.getElementById("transcript")
const endBtn = document.getElementById("endBtn")
const waveEl = document.getElementById("wave")

// WebSocket and audio state
let ws = null
let mediaRecorder = null
let audioChunks = []
let isRecording = false
let audioQueue = []
let isPlayingAudio = false

// Connect WebSocket
const WS_URL = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/call`

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

// Play audio queue sequentially so responses don't overlap
async function playAudioQueue() {
    if (isPlayingAudio || audioQueue.length === 0) return
    isPlayingAudio = true
    waveEl.classList.add("animate-pulse")

    const audioBytes = audioQueue.shift()
    const blob = new Blob([audioBytes], { type: "audio/wav" })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)

    audio.onended = () => {
        URL.revokeObjectURL(url)
        isPlayingAudio = false
        waveEl.classList.remove("animate-pulse")
        playAudioQueue()
    }

    audio.onerror = () => {
        isPlayingAudio = false
        waveEl.classList.remove("animate-pulse")
        playAudioQueue()
    }

    await audio.play().catch(e => {
        console.error("audio play error:", e)
        isPlayingAudio = false
    })
}

// Start recording mic audio
async function startRecording() {
    if (isRecording) return
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" })
        audioChunks = []

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data)
        }

        mediaRecorder.onstop = async () => {
            if (audioChunks.length === 0) return
            const blob = new Blob(audioChunks, { type: "audio/webm" })
            const buffer = await blob.arrayBuffer()
            const base64 = btoa(String.fromCharCode(...new Uint8Array(buffer)))
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "audio", data: base64 }))
            }
            audioChunks = []
        }

        mediaRecorder.start()
        isRecording = true
        setStatus("Listening...", "text-green-400")
    } catch (err) {
        setStatus("Mic access denied", "text-red-400")
        console.error("mic error:", err)
    }
}

function stopRecording() {
    if (!isRecording || !mediaRecorder) return
    mediaRecorder.stop()
    mediaRecorder.stream.getTracks().forEach(t => t.stop())
    isRecording = false
    setStatus("Processing...", "text-yellow-400")
}

// Hold to talk button
const talkBtn = document.getElementById("talkBtn")

talkBtn.addEventListener("mousedown", startRecording)
talkBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording() })
talkBtn.addEventListener("mouseup", stopRecording)
talkBtn.addEventListener("touchend", stopRecording)
talkBtn.addEventListener("mouseleave", () => { if (isRecording) stopRecording() })

// Connect WebSocket and start call
function connect() {
    ws = new WebSocket(WS_URL)

    ws.onopen = () => {
        setStatus("Connecting...", "text-yellow-400")
        ws.send(JSON.stringify({ type: "start", callerId }))
    }

    ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data)

        if (msg.type === "status") {
            if (msg.status === "listening") setStatus("Listening...", "text-green-400")
            else if (msg.status === "processing") setStatus("Processing...", "text-yellow-400")
            else if (msg.status === "speaking") setStatus("Alex is speaking...", "text-indigo-400")
        }

        else if (msg.type === "transcript") {
            addTranscript(msg.role, msg.text)
        }

        else if (msg.type === "audio") {
            // Decode base64 WAV and queue for playback
            const binary = atob(msg.data)
            const bytes = new Uint8Array(binary.length)
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
            audioQueue.push(bytes.buffer)
            playAudioQueue()
        }

        else if (msg.type === "call_ended") {
            setStatus("Call ended", "text-gray-400")
            talkBtn.disabled = true
            endBtn.disabled = true
            setTimeout(() => {
                window.location.href = `/?callEnded=1`
            }, 2000)
        }

        else if (msg.type === "error") {
            setStatus("Error: " + msg.message, "text-red-400")
        }
    }

    ws.onclose = () => {
        setStatus("Call disconnected", "text-gray-500")
        isRecording = false
    }

    ws.onerror = (e) => {
        setStatus("Connection error", "text-red-400")
        console.error("ws error:", e)
    }
}

endBtn.addEventListener("click", () => {
    if (ws) ws.send(JSON.stringify({ type: "end" }))
    setTimeout(() => window.location.href = "/", 500)
})

connect()
