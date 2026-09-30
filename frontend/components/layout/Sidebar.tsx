'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { BarChart3, Building2, ChevronDown, Database, Fence, FileText, Folder, Globe, MapPin, Plus, Search, Sparkles, Users } from 'lucide-react'
import { usePathname } from 'next/navigation'
import SearchHistoryList from '@/components/layout/SearchHistoryList'

const AGENCY_KEY = 'prop_agency_config'
const CRM_BASE_URL = 'https://demo-ali-crm.vercel.app'

type CrmLink = { label: string; path: string }

const CRM_SECTIONS: { title: string | null; links: CrmLink[] }[] = [
  {
    title: null,
    links: [
      { label: 'Dashboard', path: '/dashboard' },
      { label: 'Gastos', path: '/gastos' },
      { label: 'Leads', path: '/leads' },
    ],
  },
  {
    title: 'Mensajería',
    links: [
      { label: 'Chats', path: '/chat' },
      { label: 'Mensajes Programados', path: '/mensajes-programados' },
    ],
  },
  {
    title: 'Cartera',
    links: [
      { label: 'Propiedades', path: '/propiedades' },
      { label: 'Búsquedas', path: '/propiedades/busquedas' },
      { label: 'Campañas Activas', path: '/campanas-activas' },
    ],
  },
  {
    title: 'Asistentes',
    links: [
      { label: 'Cotizaciones', path: '/asistente?assistantId=tasador' },
      { label: 'Documentación', path: '/asistente?assistantId=ventas' },
      { label: 'Modelos', path: '/asistente?assistantId=modelos' },
    ],
  },
]

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
  const pathname = usePathname()
  const [agency, setAgency] = useState<AgencyConfig>({ nombre: '', telefono: '', whatsapp: '' })
  const [crmOpen, setCrmOpen] = useState(false)

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
        <Link
          href="/sources"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/sources')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Globe className="size-4" />
          Fuentes
        </Link>
        <Link
          href="/barrios"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/barrios')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Fence className="size-4" />
          Barrios cerrados
        </Link>
        <Link
          href="/ficha-propio"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/ficha-propio')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <FileText className="size-4" />
          Ficha Propio
        </Link>
        <Link
          href="/historial"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/historial')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Folder className="size-4" />
          Carpetas
        </Link>
        <Link
          href="/limpieza"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/limpieza')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <Sparkles className="size-4" />
          Limpieza
        </Link>
        <Link
          href="/metrics"
          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
            pathname?.startsWith('/metrics')
              ? 'bg-muted text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          }`}
        >
          <BarChart3 className="size-4" />
          Métricas
        </Link>
        <button
          type="button"
          onClick={() => setCrmOpen((open) => !open)}
          aria-expanded={crmOpen}
          className="flex w-full items-center gap-2 rounded-xl bg-black px-3 py-2 text-sm font-medium text-white transition hover:bg-black/85"
        >
          <Users className="size-4" />
          CRM
          <ChevronDown
            className={`ml-auto size-4 text-white/60 transition-transform ${crmOpen ? 'rotate-180' : ''}`}
          />
        </button>
        {crmOpen && (
          <div className="space-y-2 pb-1 pl-4 pt-1">
            {CRM_SECTIONS.map((section) => (
              <div key={section.title ?? 'general'}>
                {section.title && (
                  <p className="px-3 pb-0.5 text-[10px] font-medium uppercase tracking-wide text-sidebar-foreground/40">
                    {section.title}
                  </p>
                )}
                {section.links.map((link) => (
                  <a
                    key={link.path}
                    href={`${CRM_BASE_URL}${link.path}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex w-full items-center rounded-lg px-3 py-1.5 text-sm text-sidebar-foreground/80 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Historial */}
      <SearchHistoryList />

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
