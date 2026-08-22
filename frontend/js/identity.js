// Generates a UUID on first visit and stores it in localStorage.
// This is how we identify returning users without requiring login.
// The same callerId is sent on every WebSocket connection so all
// calls from this browser are grouped under one history.

export function getCallerId() {
    let id = localStorage.getItem("callerId")
    if (!id) {
        id = crypto.randomUUID()
        localStorage.setItem("callerId", id)
    }
    return id
}
