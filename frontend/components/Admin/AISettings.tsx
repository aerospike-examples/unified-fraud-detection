'use client'

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
    Brain,
    CheckCircle,
    AlertCircle,
    Loader2,
    Eye,
    EyeOff,
    RefreshCw,
} from 'lucide-react'
import { cn } from '@/lib/utils'

interface LLMConfig {
    provider: 'gemini' | 'mistral'
    gemini_api_key: string
    gemini_model: string
    mistral_api_key: string
    mistral_model: string
    mistral_reasoning_effort: 'none' | 'high'
    has_env_gemini_key: boolean
    has_env_mistral_key: boolean
}

const defaultConfig: LLMConfig = {
    provider: 'gemini',
    gemini_api_key: '',
    gemini_model: 'gemini-2.0-flash',
    mistral_api_key: '',
    mistral_model: 'mistral-small-latest',
    mistral_reasoning_effort: 'none',
    has_env_gemini_key: false,
    has_env_mistral_key: false,
}

export default function AISettings() {
    const [config, setConfig] = useState<LLMConfig>(defaultConfig)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
    const [saveResult, setSaveResult] = useState<{ success: boolean; message: string } | null>(null)
    const [showApiKey, setShowApiKey] = useState(false)

    const fetchConfig = useCallback(async () => {
        try {
            const res = await fetch('/api/settings/llm')
            if (res.ok) {
                const data = await res.json()
                setConfig(data)
            }
        } catch (e) {
            console.error('Failed to fetch LLM config:', e)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchConfig()
    }, [fetchConfig])

    const handleSave = async () => {
        setSaving(true)
        setSaveResult(null)
        setTestResult(null)
        try {
                const res = await fetch('/api/settings/llm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    gemini_api_key: config.gemini_api_key || undefined,
                    gemini_model: config.gemini_model,
                    mistral_api_key: config.mistral_api_key || undefined,
                    mistral_model: config.mistral_model,
                    mistral_reasoning_effort: config.mistral_reasoning_effort,
                }),
            })
            const data = await res.json()
            if (res.ok) {
                setSaveResult({ success: true, message: 'Settings saved' })
                fetchConfig()
            } else {
                setSaveResult({ success: false, message: data.detail || 'Failed to save' })
            }
        } catch {
            setSaveResult({ success: false, message: 'Connection error' })
        } finally {
            setSaving(false)
        }
    }

    const handleTest = async () => {
        setTesting(true)
        setTestResult(null)
        try {
            const res = await fetch('/api/settings/llm/test', { method: 'POST' })
            const data = await res.json()
            if (res.ok) {
                setTestResult({ success: true, message: `Connected (${data.duration_ms}ms) — ${data.response_preview}` })
            } else {
                setTestResult({ success: false, message: data.detail || 'Test failed' })
            }
        } catch {
            setTestResult({ success: false, message: 'Connection error' })
        } finally {
            setTesting(false)
        }
    }

    if (loading) {
        return (
            <Card className="bg-white border-slate-200 shadow-sm">
                <CardContent className="pt-6">
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
                    </div>
                </CardContent>
            </Card>
        )
    }

    return (
        <div className="space-y-6">
            {/* Provider Selection */}
            <Card className="bg-white border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Brain className="h-5 w-5 text-indigo-600" />
                        LLM Provider
                    </CardTitle>
                    <CardDescription>
                        Choose which AI model powers the fraud investigation agent
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-2 gap-4">
                        {/* Gemini Card */}
                        <button
                            onClick={() => setConfig(prev => ({ ...prev, provider: 'gemini' }))}
                            className={cn(
                                "relative p-5 rounded-lg border-2 text-left transition-all",
                                config.provider === 'gemini'
                                    ? "border-indigo-500 bg-indigo-50"
                                    : "border-slate-200 bg-white hover:border-slate-300"
                            )}
                        >
                            {config.provider === 'gemini' && (
                                <div className="absolute top-3 right-3">
                                    <CheckCircle className="w-5 h-5 text-indigo-600" />
                                </div>
                            )}
                            <div className="flex items-center gap-3 mb-2">
                                <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center text-lg">G</div>
                                <div>
                                    <h3 className="font-semibold text-slate-900">Google Gemini</h3>
                                    <p className="text-xs text-slate-500">Cloud API</p>
                                </div>
                            </div>
                            <p className="text-sm text-slate-600 mt-2">
                                Fast, high-quality responses via Google&apos;s Gemini API. Requires an API key.
                            </p>
                            {config.has_env_gemini_key && (
                                <Badge variant="outline" className="mt-3 text-xs border-emerald-300 text-emerald-600">
                                    API key set in env
                                </Badge>
                            )}
                        </button>

                        {/* Mistral Card */}
                        <button
                            onClick={() => setConfig(prev => ({ ...prev, provider: 'mistral' }))}
                            className={cn(
                                "relative p-5 rounded-lg border-2 text-left transition-all",
                                config.provider === 'mistral'
                                    ? "border-indigo-500 bg-indigo-50"
                                    : "border-slate-200 bg-white hover:border-slate-300"
                            )}
                        >
                            {config.provider === 'mistral' && (
                                <div className="absolute top-3 right-3">
                                    <CheckCircle className="w-5 h-5 text-indigo-600" />
                                </div>
                            )}
                            <div className="flex items-center gap-3 mb-2">
                                <div className="w-8 h-8 rounded-lg bg-orange-100 flex items-center justify-center text-lg font-bold text-orange-600">M</div>
                                <div>
                                    <h3 className="font-semibold text-slate-900">Mistral AI</h3>
                                    <p className="text-xs text-slate-500">Cloud API with Reasoning</p>
                                </div>
                            </div>
                            <p className="text-sm text-slate-600 mt-2">
                                Mistral models with built-in reasoning. Supports adjustable and native reasoning models.
                            </p>
                            {config.has_env_mistral_key && (
                                <Badge variant="outline" className="mt-3 text-xs border-emerald-300 text-emerald-600">
                                    API key set in env
                                </Badge>
                            )}
                        </button>
                    </div>
                </CardContent>
            </Card>

            {/* Configuration */}
            <Card className="bg-white border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">
                        {config.provider === 'gemini' ? 'Gemini Configuration' : 'Mistral Configuration'}
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    {config.provider === 'gemini' ? (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="gemini-key">API Key</Label>
                                <div className="relative">
                                    <Input
                                        id="gemini-key"
                                        type={showApiKey ? 'text' : 'password'}
                                        placeholder={config.has_env_gemini_key ? '••••••••  (set from environment)' : 'Enter your Gemini API key'}
                                        value={config.gemini_api_key}
                                        onChange={e => setConfig(prev => ({ ...prev, gemini_api_key: e.target.value }))}
                                        className="pr-10"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                                    >
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                {config.has_env_gemini_key && !config.gemini_api_key && (
                                    <p className="text-xs text-emerald-600">Using API key from environment variable</p>
                                )}
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="gemini-model">Model</Label>
                                <Input
                                    id="gemini-model"
                                    placeholder="gemini-2.0-flash"
                                    value={config.gemini_model}
                                    onChange={e => setConfig(prev => ({ ...prev, gemini_model: e.target.value }))}
                                />
                                <p className="text-xs text-slate-500">e.g. gemini-2.0-flash, gemini-1.5-pro, gemini-2.5-flash-preview-04-17</p>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="mistral-key">API Key</Label>
                                <div className="relative">
                                    <Input
                                        id="mistral-key"
                                        type={showApiKey ? 'text' : 'password'}
                                        placeholder={config.has_env_mistral_key ? '••••••••  (set from environment)' : 'Enter your Mistral API key'}
                                        value={config.mistral_api_key}
                                        onChange={e => setConfig(prev => ({ ...prev, mistral_api_key: e.target.value }))}
                                        className="pr-10"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                                    >
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                {config.has_env_mistral_key && !config.mistral_api_key && (
                                    <p className="text-xs text-emerald-600">Using API key from environment variable</p>
                                )}
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="mistral-model">Model</Label>
                                <Input
                                    id="mistral-model"
                                    placeholder="mistral-small-latest"
                                    value={config.mistral_model}
                                    onChange={e => setConfig(prev => ({ ...prev, mistral_model: e.target.value }))}
                                />
                                <p className="text-xs text-slate-500">
                                    <strong>Adjustable reasoning:</strong> mistral-small-latest
                                    <br />
                                    <strong>Native reasoning:</strong> magistral-small-latest, magistral-medium-latest
                                </p>
                            </div>
                            <div className="space-y-2">
                                <Label>Reasoning Effort</Label>
                                <div className="flex gap-2">
                                    <button
                                        onClick={() => setConfig(prev => ({ ...prev, mistral_reasoning_effort: 'none' }))}
                                        className={cn(
                                            "flex-1 px-3 py-2 rounded-lg border-2 text-sm font-medium transition-all",
                                            config.mistral_reasoning_effort === 'none'
                                                ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                                                : "border-slate-200 text-slate-600 hover:border-slate-300"
                                        )}
                                    >
                                        None (Fast)
                                    </button>
                                    <button
                                        onClick={() => setConfig(prev => ({ ...prev, mistral_reasoning_effort: 'high' }))}
                                        className={cn(
                                            "flex-1 px-3 py-2 rounded-lg border-2 text-sm font-medium transition-all",
                                            config.mistral_reasoning_effort === 'high'
                                                ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                                                : "border-slate-200 text-slate-600 hover:border-slate-300"
                                        )}
                                    >
                                        High (Deep Thinking)
                                    </button>
                                </div>
                                <p className="text-xs text-slate-500">
                                    {config.mistral_reasoning_effort === 'none'
                                        ? 'Faster responses, best for structured tool-calling. Recommended for investigations.'
                                        : 'Model thinks deeply before responding. Slower but may produce better analysis. Can cause parsing failures in agent loop.'}
                                </p>
                            </div>
                        </>
                    )}

                    {/* Action buttons */}
                    <div className="flex items-center gap-3 pt-4 border-t border-slate-100">
                        <Button
                            onClick={handleSave}
                            disabled={saving}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                            {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <CheckCircle className="w-4 h-4 mr-2" />}
                            Save Settings
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleTest}
                            disabled={testing}
                        >
                            {testing ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}
                            Test Connection
                        </Button>
                    </div>

                    {/* Results */}
                    {saveResult && (
                        <div className={cn(
                            "flex items-center gap-2 p-3 rounded-lg text-sm",
                            saveResult.success ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
                        )}>
                            {saveResult.success ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                            {saveResult.message}
                        </div>
                    )}
                    {testResult && (
                        <div className={cn(
                            "flex items-start gap-2 p-3 rounded-lg text-sm",
                            testResult.success ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
                        )}>
                            {testResult.success ? <CheckCircle className="w-4 h-4 mt-0.5" /> : <AlertCircle className="w-4 h-4 mt-0.5" />}
                            <span>{testResult.message}</span>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}
