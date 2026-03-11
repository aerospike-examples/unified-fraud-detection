'use client'

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CheckCircle2, AlertCircle, Loader2, Eye, EyeOff } from 'lucide-react'

interface Provider {
  id: string
  name: string
  models: string[]
}

interface LLMConfig {
  provider: string
  model: string
  base_url: string
  api_key_set: boolean
  api_key_hint: string
}

export default function AgentSetup() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [config, setConfig] = useState<LLMConfig | null>(null)

  const [selectedProvider, setSelectedProvider] = useState('')
  const [selectedModel, setSelectedModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)

  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [providersRes, configRes] = await Promise.all([
        fetch('/api/llm/providers'),
        fetch('/api/llm/config'),
      ])
      const providersData = await providersRes.json()
      const configData = await configRes.json()

      setProviders(providersData.providers || [])
      setConfig(configData)
      setSelectedProvider(configData.provider || '')
      setSelectedModel(configData.model || '')
    } catch {
      setMessage({ type: 'error', text: 'Failed to load LLM configuration' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const currentProvider = providers.find((p) => p.id === selectedProvider)

  useEffect(() => {
    if (currentProvider && !currentProvider.models.includes(selectedModel)) {
      setSelectedModel(currentProvider.models[0] || '')
    }
  }, [selectedProvider, currentProvider, selectedModel])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const body: Record<string, string> = {
        provider: selectedProvider,
        model: selectedModel,
      }
      if (apiKey) {
        body.api_key = apiKey
      }

      const res = await fetch('/api/llm/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to update config')
      }

      const updated = await res.json()
      setConfig(updated)
      setApiKey('')
      setMessage({ type: 'success', text: 'Configuration saved successfully' })
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || 'Failed to save configuration' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">Loading configuration...</span>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Current Status */}
      <Card>
        <CardHeader>
          <CardTitle>Current Configuration</CardTitle>
          <CardDescription>
            Active LLM provider and model used by the investigation agent
          </CardDescription>
        </CardHeader>
        <CardContent>
          {config ? (
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Provider</p>
                <p className="text-sm font-medium capitalize">{config.provider}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Model</p>
                <p className="text-sm font-medium font-mono">{config.model}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">API Key</p>
                <p className="text-sm font-medium font-mono">
                  {config.api_key_set ? config.api_key_hint : (
                    <span className="text-destructive">Not set</span>
                  )}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Unable to load configuration</p>
          )}
        </CardContent>
      </Card>

      {/* Configuration Form */}
      <Card>
        <CardHeader>
          <CardTitle>Update Configuration</CardTitle>
          <CardDescription>
            Select a provider and model for the investigation agent. Changes take effect immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-2 gap-6">
            {/* Provider */}
            <div className="space-y-2">
              <Label htmlFor="provider">Provider</Label>
              <select
                id="provider"
                value={selectedProvider}
                onChange={(e) => setSelectedProvider(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Model */}
            <div className="space-y-2">
              <Label htmlFor="model">Model</Label>
              <select
                id="model"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {currentProvider?.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* API Key */}
          <div className="space-y-2">
            <Label htmlFor="api-key">API Key</Label>
            <div className="relative">
              <Input
                id="api-key"
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={config?.api_key_set ? 'Leave blank to keep current key' : 'Enter your API key'}
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              {config?.api_key_set
                ? `Current key: ${config.api_key_hint}. Leave blank to keep it unchanged.`
                : 'No API key is configured. Enter one to enable the investigation agent.'}
            </p>
          </div>

          {/* Message */}
          {message && (
            <div
              className={`flex items-center gap-2 rounded-md p-3 text-sm ${
                message.type === 'success'
                  ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
                  : 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300'
              }`}
            >
              {message.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4 shrink-0" />
              ) : (
                <AlertCircle className="w-4 h-4 shrink-0" />
              )}
              {message.text}
            </div>
          )}

          {/* Save Button */}
          <div className="flex justify-end">
            <Button onClick={handleSave} disabled={saving || !selectedProvider || !selectedModel}>
              {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Save Configuration
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
