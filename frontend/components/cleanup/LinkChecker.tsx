'use client'
import { useState } from 'react'
import { Check, Copy, HelpCircle, Link2, Loader2, Trash2, XCircle } from 'lucide-react'
import { useLinkChecker, type CheckedLink, type LinkDeleteResult } from '@/hooks/useCleanup'

const PLACEHOLDER = `https://www.zonaprop.com.ar/propiedades/...
https://www.argenprop.com/departamento-en-venta/...
https://articulo.mercadolibre.com.ar/MLA-...`

export default function LinkChecker() {
  const { result, checking, error, check, reset, deleteBroken, deleting, deleted } =
    useLinkChecker()
  const [raw, setRaw] = useState('')
  const [copied, setCopied] = useState(false)
  // Borrar no se deshace: el botón pide confirmación en el lugar antes de tirar.
  const [confirming, setConfirming] = useState(false)

  const handleCheck = () => {
    setCopied(false)
    setConfirming(false)
    void check(raw)
  }

  const handleClear = () => {
    setRaw('')
    setCopied(false)
    setConfirming(false)
    reset()
  }

  const removedUrls = new Set((deleted?.eliminadas ?? []).map((p) => p.url_origen))
  const pendingBroken = (result?.rotos ?? []).filter((l) => !removedUrls.has(l.url))

  const handleDelete = async () => {
    setConfirming(false)
    await deleteBroken()
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
          reenviarlos. Verificar no toca nada; los que den rotos los podés sacar de la base con el
          botón que aparece sobre esa lista.
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
            <div className="space-y-2">
              <LinkList
                title="Rotos o eliminados"
                links={result.rotos}
                tone="bad"
                empty="Ninguno se cayó"
                removed={removedUrls}
              />

              {pendingBroken.length > 0 &&
                (confirming ? (
                  <div className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-2">
                    <p className="text-[11px] text-muted-foreground">
                      Se van a borrar de la base las propiedades detrás de {pendingBroken.length}{' '}
                      link{pendingBroken.length === 1 ? '' : 's'} roto
                      {pendingBroken.length === 1 ? '' : 's'}. Esto no se deshace. Antes de borrar
                      se entra a cada aviso una vez más: el que haya vuelto a estar publicado se
                      conserva.
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        onClick={handleDelete}
                        className="flex items-center gap-1.5 rounded-lg bg-destructive px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-destructive/85"
                      >
                        <Trash2 className="size-3.5" />
                        Sí, borrarlas
                      </button>
                      <button
                        onClick={() => setConfirming(false)}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted"
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirming(true)}
                    disabled={deleting}
                    className="flex items-center gap-1.5 rounded-lg border border-destructive/40 px-2.5 py-1.5 text-xs font-medium text-destructive transition hover:bg-destructive/10 disabled:opacity-40"
                  >
                    {deleting ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="size-3.5" />
                    )}
                    {deleting
                      ? 'Revisando y borrando...'
                      : `Borrar de la base (${pendingBroken.length})`}
                  </button>
                ))}

              {deleted && <DeleteSummary result={deleted} />}
            </div>
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

function DeleteSummary({ result }: { result: LinkDeleteResult }) {
  const { eliminadas, conservadas, no_encontradas } = result

  return (
    <div className="space-y-1 rounded-lg bg-muted/40 p-2 text-[11px] text-muted-foreground">
      <p className="font-medium text-foreground">
        {eliminadas.length} propiedad{eliminadas.length === 1 ? '' : 'es'} eliminada
        {eliminadas.length === 1 ? '' : 's'} de la base
      </p>
      {conservadas.length > 0 && (
        <p>
          {conservadas.length} se conservó{conservadas.length === 1 ? '' : 'n'}: al revisarla
          {conservadas.length === 1 ? '' : 's'} de nuevo no dio{conservadas.length === 1 ? '' : 'ron'}{' '}
          caída{conservadas.length === 1 ? '' : 's'} — seguía publicada o el portal no respondió.
        </p>
      )}
      {no_encontradas.length > 0 && (
        <p>
          {no_encontradas.length} link{no_encontradas.length === 1 ? '' : 's'} no estaba
          {no_encontradas.length === 1 ? '' : 'n'} guardado
          {no_encontradas.length === 1 ? '' : 's'} en la base — no había nada que borrar.
        </p>
      )}
    </div>
  )
}

function LinkList({
  title,
  links,
  tone,
  empty,
  removed,
}: {
  title: string
  links: CheckedLink[]
  tone: keyof typeof TONE
  empty: string
  removed?: Set<string>
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
          {links.map((link) => {
            const gone = removed?.has(link.url) ?? false
            return (
              <li key={link.url} className="min-w-0">
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`block truncate font-mono text-xs hover:underline ${
                    gone ? 'text-muted-foreground/60 line-through' : 'text-foreground'
                  }`}
                  title={link.url}
                >
                  {link.url}
                </a>
                <p className="truncate text-[11px] text-muted-foreground">
                  {link.motivo}
                  {gone && (
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-foreground">
                      borrada de la base
                    </span>
                  )}
                </p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
