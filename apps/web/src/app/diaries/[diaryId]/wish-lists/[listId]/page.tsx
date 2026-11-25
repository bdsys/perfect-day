'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import AppHeader from '@/components/AppHeader'
import WishListItemRow, { WishListItemFormFields, itemFieldsToBody } from '@/components/WishListItemRow'
import WishListLinkBox from '@/components/WishListLinkBox'
import { api, ApiError, type Diary, type WishList, type WishListItemFields } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatListDate } from '@/lib/date'
import { readOnlyBanner } from '@/lib/plan'

const EMPTY_ITEM = { name: '', url: '', note: '', many: false }

function pastedListHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export default function WishListPage() {
  const { diaryId, listId } = useParams<{ diaryId: string; listId: string }>()
  const { user, loading: authLoading } = useAuth()
  const router = useRouter()
  const [diary, setDiary] = useState<Diary | null>(null)
  const [list, setList] = useState<WishList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [details, setDetails] = useState({ title: '', list_date: '', pasted_list_url: '' })
  const [newItem, setNewItem] = useState(EMPTY_ITEM)
  // 'owner': the signed-in owner is asked for a name; 'editor': told to ask the owner.
  const [needsName, setNeedsName] = useState<'owner' | 'editor' | null>(null)
  const [name, setName] = useState('')

  useEffect(() => {
    if (!authLoading && !user) router.replace('/login')
  }, [user, authLoading, router])

  const take = useCallback((l: WishList) => {
    setList(l)
    setDetails({ title: l.title, list_date: l.list_date, pasted_list_url: l.pasted_list_url ?? '' })
  }, [])

  const reload = useCallback(async () => {
    take(await api.wishLists.get(listId))
  }, [listId, take])

  useEffect(() => {
    if (!user || !diaryId || !listId) return
    ;(async () => {
      try {
        const [d, l] = await Promise.all([api.diaries.get(diaryId), api.wishLists.get(listId)])
        setDiary(d)
        take(l)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to load the wish list')
      } finally {
        setLoading(false)
      }
    })()
  }, [user, diaryId, listId, take])

  async function run(action: () => Promise<void>) {
    setError('')
    setBusy(true)
    try {
      await action()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  const publish = () =>
    run(async () => {
      try {
        take(await api.wishLists.publish(listId))
        setNeedsName(null)
      } catch (e) {
        if (e instanceof ApiError && e.code === 'display_name_required') {
          setNeedsName(e.details?.caller_is_owner ? 'owner' : 'editor')
          return
        }
        throw e
      }
    })

  const saveNameAndPublish = (ev: React.FormEvent) => {
    ev.preventDefault()
    return run(async () => {
      await api.auth.updateProfile({ display_name: name.trim() })
      take(await api.wishLists.publish(listId))
      setNeedsName(null)
    })
  }

  const saveDetails = (ev: React.FormEvent) => {
    ev.preventDefault()
    return run(async () =>
      take(
        await api.wishLists.update(listId, {
          title: details.title.trim(),
          list_date: details.list_date,
          pasted_list_url: details.pasted_list_url.trim() || null,
        }),
      ),
    )
  }

  const addItem = (ev: React.FormEvent) => {
    ev.preventDefault()
    return run(async () => {
      await api.wishLists.addItem(listId, itemFieldsToBody(newItem))
      setNewItem(EMPTY_ITEM)
      await reload()
    })
  }

  const saveItem = (itemId: string, fields: WishListItemFields) =>
    run(async () => {
      await api.wishLists.updateItem(itemId, fields)
      await reload()
    })

  const removeItem = (itemId: string, itemName: string) => {
    if (!confirm(`Delete ${itemName}? Anyone who was getting it is let go.`)) return
    return run(async () => {
      await api.wishLists.removeItem(itemId)
      await reload()
    })
  }

  const reloadAfter = (call: () => Promise<unknown>) =>
    run(async () => {
      await call()
      await reload()
    })

  const removeList = () => {
    if (!list || !confirm(`Delete ${list.title}? Its link stops working.`)) return
    return run(async () => {
      await api.wishLists.remove(listId)
      router.push(`/diaries/${diaryId}/wish-lists`)
    })
  }

  if (authLoading || loading) return <div className="loading">Loading…</div>
  if (!user) return null

  const manager = diary != null && diary.role !== 'viewer'
  const readOnly = manager && (diary?.plan?.read_only ?? false)
  const published = list?.status === 'published'

  return (
    <>
      <AppHeader />
      <div className="container" style={{ paddingTop: '1.5rem' }}>
        <p>
          <Link href={`/diaries/${diaryId}/wish-lists`}>← Wish lists</Link>
        </p>
        {error && <p className="error-message" role="alert" style={{ marginBottom: '1rem' }}>{error}</p>}
        {list && (
          <>
            <h1 className="page-title">{list.title}</h1>
            <p>
              {formatListDate(list.list_date)}
              {manager && ` · ${published ? 'Published' : 'Draft'}`}
            </p>
            {list.pasted_list_url && (
              <p>
                <a href={list.pasted_list_url} target="_blank" rel="noopener noreferrer">
                  {`${list.title} list on ${pastedListHost(list.pasted_list_url)}`}
                </a>
              </p>
            )}

            {readOnly && (
              <div className="error-message" style={{ marginBottom: '1rem' }}>
                <p style={{ margin: 0 }}>{readOnlyBanner(diary?.plan?.plan_name ?? null)}</p>
                <Link href="/plans" className="plan-upgrade">Upgrade now</Link>
              </div>
            )}

            {manager && (
              <>
                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
                  {published ? (
                    <button type="button" className="btn btn-secondary" onClick={() => reloadAfter(() => api.wishLists.unpublish(listId))} disabled={busy}>
                      Unpublish
                    </button>
                  ) : (
                    <button type="button" className="btn btn-primary" onClick={publish} disabled={busy || readOnly}>
                      Publish
                    </button>
                  )}
                  <button type="button" className="btn btn-secondary" onClick={removeList} disabled={busy}>
                    Delete list
                  </button>
                </div>

                {needsName === 'editor' && (
                  <p role="status">{`Ask ${diary?.owner_name ?? 'the owner'} to add their name in Profile first.`}</p>
                )}
                {needsName === 'owner' && (
                  <form onSubmit={saveNameAndPublish} style={{ marginBottom: '1rem' }}>
                    <label>
                      Your name, as people with the link will see it
                      <input type="text" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
                    </label>
                    <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
                      Save and publish
                    </button>
                  </form>
                )}

                {published && (
                  <WishListLinkBox
                    list={list}
                    busy={busy}
                    readOnly={readOnly}
                    onLinkOff={() => reloadAfter(() => api.wishLists.linkOff(listId))}
                    onNewLink={() => reloadAfter(() => api.wishLists.newLink(listId))}
                    onError={setError}
                  />
                )}

                <form onSubmit={saveDetails} aria-label="List details" style={{ marginBottom: '1.5rem' }}>
                  <label>
                    Title
                    <input type="text" required maxLength={120} value={details.title} onChange={(e) => setDetails({ ...details, title: e.target.value })} />
                  </label>
                  <label>
                    Date
                    <input type="date" required value={details.list_date} onChange={(e) => setDetails({ ...details, list_date: e.target.value })} />
                  </label>
                  <label>
                    Pasted list link (optional)
                    <input type="url" maxLength={2048} value={details.pasted_list_url} onChange={(e) => setDetails({ ...details, pasted_list_url: e.target.value })} />
                  </label>
                  <button type="submit" className="btn btn-primary" disabled={busy || readOnly || !details.title.trim()}>
                    Save details
                  </button>
                </form>
              </>
            )}

            <section style={{ marginBottom: '1.5rem' }}>
              <h2>Items</h2>
              {list.items.length === 0 && <p>No items yet.</p>}
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {list.items.map((it) => (
                  <WishListItemRow
                    key={it.id}
                    item={it}
                    manager={manager}
                    readOnly={readOnly}
                    published={!!published}
                    busy={busy}
                    onClaim={() => reloadAfter(() => api.wishLists.claim(it.id))}
                    onUnclaim={() => reloadAfter(() => api.wishLists.unclaim(it.id))}
                    onRelease={(claimId) => reloadAfter(() => api.wishLists.release(claimId))}
                    onSave={(fields) => saveItem(it.id, fields)}
                    onDelete={() => removeItem(it.id, it.name)}
                  />
                ))}
              </ul>
            </section>

            {manager && (
              <form onSubmit={addItem} aria-label="Add item">
                <WishListItemFormFields value={newItem} onChange={setNewItem} />
                <button type="submit" className="btn btn-primary" disabled={busy || readOnly || !newItem.name.trim()}>
                  Add item
                </button>
              </form>
            )}
          </>
        )}
      </div>
    </>
  )
}
