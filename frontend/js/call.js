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
    while (audioQueue.length > 0) {
        const buf = audioQueue.shift()
        await playAudioBytes(buf)
    }
    isPlayingAudio = false
    setStatus("Listening... hold button to speak", "text-green-400")
}

function getSupportedMimeType() {
    const types = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
        ""
    ]
    for (const t of types) {
        if (!t || MediaRecorder.isTypeSupported(t)) return t
    }
    return ""
}

// Use FileReader to safely convert blob to base64 — avoids btoa crash on large audio
function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => {
            // result is "data:audio/webm;base64,XXXXXX" — extract just the base64 part
            const base64 = reader.result.split(",")[1]
            resolve(base64)
        }
        reader.onerror = reject
        reader.readAsDataURL(blob)
    })
}

async function startRecording() {
    if (isRecording) return
    unlockAudio()

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const mimeType = getSupportedMimeType()
        const options = mimeType ? { mimeType } : {}

        mediaRecorder = new MediaRecorder(stream, options)
        audioChunks = []

        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) audioChunks.push(e.data)
        }

        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop())

            if (audioChunks.length === 0) {
                console.warn("no audio chunks")
                setStatus("Listening... hold button to speak", "text-green-400")
                return
            }

            const mimeUsed = mediaRecorder.mimeType || "audio/webm"
            const blob = new Blob(audioChunks, { type: mimeUsed })
            console.log(`audio blob: ${blob.size} bytes, type: ${mimeUsed}`)

            if (blob.size < 500) {
                setStatus("Too short — hold longer", "text-yellow-400")
                setTimeout(() => setStatus("Listening... hold button to speak", "text-green-400"), 1500)
                return
            }

            try {
                const base64 = await blobToBase64(blob)
                console.log(`sending base64: ${base64.length} chars`)
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "audio", data: base64 }))
                } else {
                    console.error("WebSocket not open")
                    setStatus("Disconnected", "text-red-400")
                }
            } catch (err) {
                console.error("base64 conversion error:", err)
                setStatus("Error sending audio", "text-red-400")
            }

            audioChunks = []
        }

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
    isRecording = false
    talkBtn.classList.remove("bg-red-600", "border-red-400")
    talkBtn.classList.add("bg-indigo-600", "border-indigo-400")
    talkBtn.textContent = "Hold to Talk"
    setStatus("Processing...", "text-yellow-400")
}

talkBtn.addEventListener("mousedown", (e) => { e.preventDefault(); startRecording() })
talkBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording() }, { passive: false })
talkBtn.addEventListener("mouseup", stopRecording)
talkBtn.addEventListener("touchend", stopRecording)
talkBtn.addEventListener("mouseleave", () => { if (isRecording) stopRecording() })

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

        console.log("ws message:", msg.type, msg.status || "")

        if (msg.type === "status") {
            if (msg.status === "listening") setStatus("Listening... hold button to speak", "text-green-400")
            else if (msg.status === "processing") setStatus("Processing...", "text-yellow-400")
            else if (msg.status === "speaking") setStatus("Alex is speaking...", "text-indigo-400")
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
            setStatus("Call ended", "text-gray-400")
            talkBtn.disabled = true
            endBtn.disabled = true
            setTimeout(() => { window.location.href = "/" }, 2000)
        }
        else if (msg.type === "error") {
            setStatus("Error: " + msg.message, "text-red-400")
            console.error("server error:", msg.message)
        }
    }

    ws.onclose = (e) => {
        console.log("WebSocket closed:", e.code, e.reason)
        setStatus("Disconnected", "text-gray-500")
    }

    ws.onerror = (e) => {
        console.error("WebSocket error:", e)
        setStatus("Connection error", "text-red-400")
    }
}

endBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "end" }))
    }
    setTimeout(() => { window.location.href = "/" }, 500)
})

document.addEventListener("click", unlockAudio, { once: true })
document.addEventListener("touchstart", unlockAudio, { once: true })

connect()
