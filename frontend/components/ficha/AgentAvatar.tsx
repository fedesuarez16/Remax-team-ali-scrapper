'use client'

import { useState } from 'react'
import Image from 'next/image'
import type { Agente } from '@/lib/ficha'
import { cn } from '@/lib/utils'

/** Un solo retrato por perfil, compartido por selector, vista previa y ficha pública. */
export function AgentAvatar({ agente, className }: { agente: Agente; className?: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  return (
    <span className={cn('relative inline-flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-sm font-semibold text-muted-foreground ring-1 ring-border', className)}>
      {agente.foto && failedSrc !== agente.foto ? (
        <Image
          src={agente.foto}
          alt={`Retrato de ${agente.nombre}`}
          width={320}
          height={480}
          sizes="200px"
          className="absolute h-auto max-w-none"
          style={agente.encuadre ?? { width: '100%', left: '0', top: '0' }}
          onError={() => setFailedSrc(agente.foto)}
        />
      ) : (
        <span role="img" aria-label={agente.nombre}>
          {agente.nombre.split(' ').map((part) => part[0]).join('')}
        </span>
      )}
    </span>
  )
}
