'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Building2, Clock, Database, MapPin, Plus, Search } from 'lucide-react'
import { usePathname } from 'next/navigation'
import { useSearchHistory } from '@/hooks/useSearchHistory'

const AGENCY_KEY = 'prop_agency_config'

type AgencyConfig = {
  nombre: string
  telefono: string
  whatsapp: string
}

function readAgencyConfig(): AgencyConfig {
  if (typeof window === 'undefined') return { nombre: '', telefono: '', whatsapp: '' }
  try {
    const raw = localStorage.getItem(AGENCY_KEY)
    return raw ? (JSON.parse(raw) as AgencyConfig) : { nombre: '', telefono: '', whatsapp: '' }
  } catch {
    return { nombre: '', telefono: '', whatsapp: '' }
  }
}

export default function Sidebar() {
  const router = useRouter()
  const pathname = usePathname()
  const { searches } = useSearchHistory()
  const [agency, setAgency] = useState<AgencyConfig>({ nombre: '', telefono: '', whatsapp: '' })

  useEffect(() => {
    setAgency(readAgencyConfig())
  }, [])

  const handleAgencyChange = (field: keyof AgencyConfig, value: string) => {
    setAgency((prev) => ({ ...prev, [field]: value }))
  }

  const handleAgencyBlur = (field: keyof AgencyConfig) => {
    const updated = { ...agency }
    try {
      localStorage.setItem(AGENCY_KEY, JSON.stringify(updated))
    } catch {
      // Ignore storage errors
    }
  }

  return (
    <aside className="hidden md:flex w-[260px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">

      {/* Brand header */}
      <div className="flex items-center gap-3 border-b border-sidebar-border px-4 py-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
          <Building2 className="size-4 text-background" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">PropSearch AI</p>
          <p className="text-xs text-sidebar-foreground/60 truncate">Buscador de propiedades</p>
        </div>
      </div>

      {/* Nueva búsqueda */}
      <div className="space-y-1 px-3 pt-3">
        <Link
          href="/chat"
          className="flex w-full items-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85"
        >
          <Plus className="size-4" />
          Nueva búsqueda
        </Link>
        <Link
          href="/properties"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/properties')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Database className="size-4" />
          Todas las propiedades
        </Link>
        <Link
          href="/search"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/search')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Search className="size-4" />
          Buscar en DB
        </Link>
        <Link
          href="/map"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/map')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <MapPin className="size-4" />
          Mapa
        </Link>
      </div>

      {/* Historial */}
      <div className="flex-1 overflow-y-auto px-3 pt-4">
        <div className="mb-2 flex items-center gap-2">
          <Clock className="size-3.5 text-sidebar-foreground/50" />
          <p className="text-xs font-medium text-sidebar-foreground/60 uppercase tracking-wide">Historial</p>
        </div>

        {searches.length === 0 ? (
          <p className="px-1 text-xs text-sidebar-foreground/40">Sin búsquedas aún</p>
        ) : (
          <ul className="space-y-0.5">
            {searches.map((entry) => (
              <li key={entry.id}>
                <button
                  onClick={() =>
                    entry.job_id
                      ? router.push(`/properties?job_id=${encodeURIComponent(entry.job_id)}`)
                      : router.push(`/chat?q=${encodeURIComponent(entry.query)}`)
                  }
                  className="flex w-full items-center gap-2 overflow-hidden rounded-lg px-2 py-1.5 text-left transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
                  title={entry.query}
                >
                  <Search className="size-3 shrink-0 text-sidebar-foreground/30 group-hover:text-sidebar-foreground/60" />
                  <span className="truncate text-xs text-sidebar-foreground/70 group-hover:text-sidebar-accent-foreground">
                    {entry.query}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Mi Inmobiliaria */}
      <div className="border-t border-sidebar-border px-3 py-3">
        <div className="mb-2 flex items-center gap-2">
          <Building2 className="size-3.5 text-sidebar-foreground/50" />
          <p className="text-xs font-medium text-sidebar-foreground/60 uppercase tracking-wide">Mi Inmobiliaria</p>
        </div>
        <div className="space-y-1.5">
          <input
            type="text"
            placeholder="Nombre"
            value={agency.nombre}
            onChange={(e) => handleAgencyChange('nombre', e.target.value)}
            onBlur={() => handleAgencyBlur('nombre')}
            className="w-full rounded-lg border border-sidebar-border bg-sidebar-accent/50 px-2.5 py-1.5 text-xs text-sidebar-foreground placeholder:text-sidebar-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20 focus:border-foreground/30"
          />
          <input
            type="tel"
            placeholder="Teléfono"
            value={agency.telefono}
            onChange={(e) => handleAgencyChange('telefono', e.target.value)}
            onBlur={() => handleAgencyBlur('telefono')}
            className="w-full rounded-lg border border-sidebar-border bg-sidebar-accent/50 px-2.5 py-1.5 text-xs text-sidebar-foreground placeholder:text-sidebar-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20 focus:border-foreground/30"
          />
          <input
            type="tel"
            placeholder="WhatsApp"
            value={agency.whatsapp}
            onChange={(e) => handleAgencyChange('whatsapp', e.target.value)}
            onBlur={() => handleAgencyBlur('whatsapp')}
            className="w-full rounded-lg border border-sidebar-border bg-sidebar-accent/50 px-2.5 py-1.5 text-xs text-sidebar-foreground placeholder:text-sidebar-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20 focus:border-foreground/30"
          />
        </div>
      </div>
    </aside>
  )
}
