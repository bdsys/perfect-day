'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'

export default function Home() {
  const { user, loading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!loading) {
      if (user) {
        router.replace('/diaries')
      } else {
        router.replace('/login')
      }
    }
  }, [user, loading, router])

  return <div className="loading">Loading…</div>
}
