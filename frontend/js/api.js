const BASE = "/api"

const apiFetch = async (endpoint, options = {}) => {
    const res = await fetch(`${BASE}${endpoint}`, {
        ...options,
        headers: { "Content-Type": "application/json", ...options.headers }
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.message || "Request failed")
    return data
}

export const getCalls = (callerId) =>
    apiFetch(`/calls?callerId=${encodeURIComponent(callerId)}`)

export const getCall = (callId) =>
    apiFetch(`/calls/${callId}`)

export const deleteCall = (callId) =>
    apiFetch(`/calls/${callId}`, { method: "DELETE" })

export const clearCalls = (callerId) =>
    apiFetch(`/calls?callerId=${encodeURIComponent(callerId)}`, { method: "DELETE" })
