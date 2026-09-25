import { UnlockForm } from './UnlockForm'

export default async function UnlockPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams
  return <UnlockForm next={next ?? '/'} />
}
