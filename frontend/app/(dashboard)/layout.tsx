import Sidebar from '@/components/layout/Sidebar'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden bg-background print:block print:h-auto print:overflow-visible print:[&>aside]:hidden">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-hidden print:overflow-visible">{children}</main>
    </div>
  )
}
