'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useState } from 'react'
import { Activity, BarChart3, RefreshCw, Database, Shield, Bot } from 'lucide-react'
import Performance from '@/components/Admin/Performance'
import Generation from '@/components/Admin/Generation'
import DataManagement from '@/components/Admin/DataManagement'
import FraudDetection from '@/components/Admin/FraudDetection'
import AgentSetup from '@/components/Admin/AgentSetup'

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
				<TabsList className="grid w-full grid-cols-5">
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
	
				<TabsTrigger value="performance" className="flex items-center space-x-2">
					<BarChart3 className="w-4 h-4" />
					<span>Performance</span>
				</TabsTrigger>
				<TabsTrigger value="agent-setup" className="flex items-center space-x-2">
					<Bot className="w-4 h-4" />
					<span>Agent Setup</span>
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
		<TabsContent forceMount value="performance" className={`space-y-4 ${active !== 'performance' ? 'hidden' : ''}`}>
			<Performance />                
		</TabsContent>
		<TabsContent forceMount value="agent-setup" className={`space-y-4 ${active !== 'agent-setup' ? 'hidden' : ''}`}>
			<AgentSetup />
		</TabsContent>
		</Tabs>
    	</div>
  	)
} 