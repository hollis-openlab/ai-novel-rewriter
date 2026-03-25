import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

function ensureLocalStorage(): void {
  if (typeof window === 'undefined') return
  if (
    typeof window.localStorage?.clear === 'function'
    && typeof window.localStorage?.getItem === 'function'
    && typeof window.localStorage?.setItem === 'function'
  ) {
    return
  }

  const memory = new Map<string, string>()
  const fallbackStorage: Storage = {
    get length() {
      return memory.size
    },
    clear() {
      memory.clear()
    },
    getItem(key: string): string | null {
      return memory.has(key) ? memory.get(key) ?? null : null
    },
    key(index: number): string | null {
      return Array.from(memory.keys())[index] ?? null
    },
    removeItem(key: string): void {
      memory.delete(key)
    },
    setItem(key: string, value: string): void {
      memory.set(key, String(value))
    },
  }

  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: fallbackStorage,
  })
}

ensureLocalStorage()

afterEach(() => {
  cleanup()
})
