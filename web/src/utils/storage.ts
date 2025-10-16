/**
 * Safe localStorage utilities with error handling
 */

export const storage = {
  /**
   * Safely get an item from localStorage
   */
  getItem: (key: string): string | null => {
    try {
      return localStorage.getItem(key)
    } catch (error) {
      console.warn('Failed to read from localStorage:', error)
      return null
    }
  },

  /**
   * Safely set an item in localStorage
   */
  setItem: (key: string, value: string): boolean => {
    try {
      localStorage.setItem(key, value)
      return true
    } catch (error) {
      console.warn('Failed to write to localStorage:', error)
      return false
    }
  },

  /**
   * Safely remove an item from localStorage
   */
  removeItem: (key: string): boolean => {
    try {
      localStorage.removeItem(key)
      return true
    } catch (error) {
      console.warn('Failed to remove from localStorage:', error)
      return false
    }
  },

  /**
   * Safely clear all localStorage
   */
  clear: (): boolean => {
    try {
      localStorage.clear()
      return true
    } catch (error) {
      console.warn('Failed to clear localStorage:', error)
      return false
    }
  },

  /**
   * Check if localStorage is available
   */
  isAvailable: (): boolean => {
    try {
      const test = '__storage_test__'
      localStorage.setItem(test, test)
      localStorage.removeItem(test)
      return true
    } catch {
      return false
    }
  }
}

/**
 * In-memory storage fallback when localStorage is not available
 */
class MemoryStorage {
  private store: Map<string, string> = new Map()

  getItem(key: string): string | null {
    return this.store.get(key) || null
  }

  setItem(key: string, value: string): boolean {
    this.store.set(key, value)
    return true
  }

  removeItem(key: string): boolean {
    this.store.delete(key)
    return true
  }

  clear(): boolean {
    this.store.clear()
    return true
  }

  isAvailable(): boolean {
    return true
  }
}

export const memoryStorage = new MemoryStorage()

/**
 * Get the best available storage (localStorage or memory fallback)
 */
export const getStorage = () => {
  return storage.isAvailable() ? storage : memoryStorage
}