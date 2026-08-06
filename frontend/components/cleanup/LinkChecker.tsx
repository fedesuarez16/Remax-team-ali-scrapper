'use client'
import { useState } from 'react'
import { Check, Copy, HelpCircle, Link2, Loader2, XCircle } from 'lucide-react'
import { useLinkChecker, type CheckedLink } from '@/hooks/useCleanup'

const PLACEHOLDER = `https://www.zonaprop.com.ar/propiedades/...
https://www.argenprop.com/departamento-en-venta/...
https://articulo.mercadolibre.com.ar/MLA-...`

export default function LinkChecker() {
  const { result, checking, error, check, reset } = useLinkChecker()
  const [raw, setRaw] = useState('')
  const [copied, setCopied] = useState(false)

  const handleCheck = () => {
    setCopied(false)
    void check(raw)
  }

  const handleClear = () => {
    setRaw('')
    setCopied(false)
    reset()
  }

  const copyActive = async () => {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result.activos.map((l) => l.url).join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Sin permiso de portapapeles: la lista igual queda visible para copiar a mano.
    }
  }

  return (
    <section className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div>
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Link2 className="size-4" />
          Verificar una lista de links
        </h2>
        <p className="text-xs text-muted-foreground">
          Pegá los links que le mandaste a un cliente y fijate cuáles siguen funcionando antes de
          reenviarlos. Esto no borra nada de la base — sólo revisa y clasifica.
        </p>
      </div>

      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        rows={5}
        placeholder={PLACEHOLDER}
        className="w-full resize-y rounded-xl border border-border bg-background px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-foreground/20"
      />

      <div className="flex items-center gap-2">
        <button
          onClick={handleCheck}
          disabled={checking || !raw.trim()}
          className="flex items-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
        >
          {checking ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
          Verificar links
        </button>
        {(result || raw) && (
          <button
            onClick={handleClear}
            className="rounded-xl border border-border px-3 py-2 text-sm font-medium transition hover:bg-muted"
          >
            Limpiar
          </button>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {checking && (
        <p className="text-xs text-muted-foreground">Entrando a cada link, esto puede tardar...</p>
      )}

      {result && !checking && (
        <div className="space-y-3 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {result.total} link{result.total === 1 ? '' : 's'} verificado
              {result.total === 1 ? '' : 's'}
            </p>
            {result.activos.length > 0 && (
              <button
                onClick={copyActive}
                className="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1 text-xs font-medium transition hover:bg-muted"
              >
                {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
                {copied ? 'Copiado' : 'Copiar activos'}
              </button>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <LinkList
              title="Activos"
              links={result.activos}
              tone="ok"
              empty="Ninguno sigue publicado"
            />
            <LinkList
              title="Rotos o eliminados"
              links={result.rotos}
              tone="bad"
              empty="Ninguno se cayó"
            />
          </div>

          {result.sin_definir.length > 0 && (
            <div className="rounded-xl bg-muted/40 p-3">
              <LinkList
                title="No se pudo verificar"
                links={result.sin_definir}
                tone="unknown"
                empty=""
              />
              <p className="mt-2 text-[11px] text-muted-foreground">
                El portal no respondió o nos bloqueó. NO están rotos necesariamente — conviene
                abrirlos a mano antes de descartarlos.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

const TONE = {
  ok: { icon: Check, color: 'text-emerald-600 dark:text-emerald-400' },
  bad: { icon: XCircle, color: 'text-destructive' },
  unknown: { icon: HelpCircle, color: 'text-muted-foreground' },
} as const

function LinkList({
  title,
  links,
  tone,
  empty,
}: {
  title: string
  links: CheckedLink[]
  tone: keyof typeof TONE
  empty: string
}) {
  const { icon: Icon, color } = TONE[tone]

  return (
    <div>
      <p className={`mb-1.5 flex items-center gap-1.5 text-xs font-semibold ${color}`}>
        <Icon className="size-3.5" />
        {title} ({links.length})
      </p>
      {links.length === 0 ? (
        empty ? <p className="text-xs text-muted-foreground">{empty}</p> : null
      ) : (
        <ul className="space-y-1.5">
          {links.map((link) => (
            <li key={link.url} className="min-w-0">
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate font-mono text-xs text-foreground hover:underline"
                title={link.url}
              >
                {link.url}
              </a>
              <p className="truncate text-[11px] text-muted-foreground">{link.motivo}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
