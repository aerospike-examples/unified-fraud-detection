'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useState } from 'react'
import { Activity, RefreshCw, Database, Shield, Brain } from 'lucide-react'
import Generation from '@/components/Admin/Generation'
import DataManagement from '@/components/Admin/DataManagement'
import FraudDetection from '@/components/Admin/FraudDetection'
import AISettings from '@/components/Admin/AISettings'

export default function AdminPage() {
	const [active, setActive] = useState('data');
	const [isGenerating, setIsGenerating] = useState(false);
	
	return (
    	<div className="space-y-6">
      		<div className="flex items-center justify-between">
				<div>
					<h1 className="text-3xl font-bold tracking-tight">Admin Panel</h1>
					<p className="text-muted-foreground">
						Manage transaction generation and fraud detection scenarios
					</p>
				</div>
				<h3 className="text-xl font-medium tracking-tight flex gap-4 items-center mr-2">
					Generating:
					{isGenerating ? <RefreshCw className='w-6 h-6 animate-spin text-green-600' /> : <div className='w-6 h-6'>🛑</div>}
				</h3>
      		</div>
			<Tabs value={active} onValueChange={setActive} className="space-y-4">
				<TabsList className="grid w-full grid-cols-4">
				<TabsTrigger value="data" className="flex items-center space-x-2">
						<Database className="w-4 h-4" />
						<span>Data Management</span>
					</TabsTrigger>
					<TabsTrigger value="generation" className="flex items-center space-x-2">
						<Activity className="w-4 h-4" />
						<span>RT Transaction Generation</span>
					</TabsTrigger>
					<TabsTrigger value="fraud-detection" className="flex items-center space-x-2">
						<Shield className="w-4 h-4" />
						<span>Fraud Detection Rules</span>
					</TabsTrigger>
	
					<TabsTrigger value="ai-settings" className="flex items-center space-x-2">
						<Brain className="w-4 h-4" />
						<span>AI Settings</span>
					</TabsTrigger>
				</TabsList>
			<TabsContent forceMount value="data" className={`space-y-4 ${active !== 'data' ? 'hidden' : ''}`}>
				<DataManagement />
			</TabsContent>
			<TabsContent forceMount value="generation" className={`space-y-4 ${active !== 'generation' ? 'hidden' : ''}`}>
				<Generation isGenerating={isGenerating} setIsGenerating={setIsGenerating} />
			</TabsContent>
			<TabsContent forceMount value="fraud-detection" className={`space-y-4 ${active !== 'fraud-detection' ? 'hidden' : ''}`}>
				<FraudDetection />
			</TabsContent>
			<TabsContent forceMount value="ai-settings" className={`space-y-4 ${active !== 'ai-settings' ? 'hidden' : ''}`}>
				<AISettings />
			</TabsContent>
			</Tabs>
    	</div>
  	)
} 