import type { WSMessage } from '@/types'

type MessageHandler = (msg: WSMessage) => void

class WSManager {
  private ws: WebSocket | null = null
  private handlers: Set<MessageHandler> = new Set()
  private subscriptions: Set<string> = new Set()
  private reconnectDelay = 1000
  private maxDelay = 30000
  private pingInterval: ReturnType<typeof setInterval> | null = null
  private isIntentionallyClosed = false

  connect() {
    if (this.ws && this.ws.readyState !== WebSocket.CLOSED) {
      return // Already connecting or connected
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    this.ws = new WebSocket(`${protocol}//${location.host}/ws/progress`)

    this.ws.onopen = () => {
      // Connected
      this.reconnectDelay = 1000

      // Re-subscribe after reconnect
      this.subscriptions.forEach(id => this.subscribe(id))

      // Setup ping/pong
      this.pingInterval = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'pong' }))
        }
      }, 25000)
    }

    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WSMessage

        // Handle ping from server, respond with pong
        if (msg.type === 'ping') {
          this.ws?.send(JSON.stringify({ type: 'pong' }))
          return
        }

        // Forward all other messages to handlers
        this.handlers.forEach(h => h(msg))
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error)
      }
    }

    this.ws.onclose = () => {
      // Disconnected
      if (this.pingInterval) {
        clearInterval(this.pingInterval)
        this.pingInterval = null
      }

      if (!this.isIntentionallyClosed) {
        setTimeout(() => {
          this.connect()
          this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxDelay)
        }, this.reconnectDelay)
      }
    }

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error)
    }
  }

  disconnect() {
    this.isIntentionallyClosed = true
    if (this.pingInterval) {
      clearInterval(this.pingInterval)
      this.pingInterval = null
    }
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect')
      this.ws = null
    }
  }

  subscribe(novelId: string | '*') {
    this.subscriptions.add(novelId)
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'subscribe', novel_id: novelId }))
    }
  }

  unsubscribe(novelId: string) {
    this.subscriptions.delete(novelId)
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'unsubscribe', novel_id: novelId }))
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN
  }

  get readyState() {
    return this.ws?.readyState || WebSocket.CLOSED
  }
}

export const wsManager = new WSManager()

// Legacy compatibility export
export type WSMessageHandler = MessageHandler