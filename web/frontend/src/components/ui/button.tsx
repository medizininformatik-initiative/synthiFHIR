import * as React from 'react'
import { Tooltip } from '../../Help'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const buttonVariants = cva('inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 disabled:pointer-events-none disabled:opacity-50 h-[var(--control-height)] px-4', {
  variants: { variant: { default: 'bg-teal-800 text-white hover:bg-teal-900', outline: 'border border-slate-300 bg-white hover:bg-slate-100' } },
  defaultVariants: { variant: 'default' },
})
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean; tooltip?: string }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, asChild = false, tooltip, ...props }, ref) => {
  const Comp = asChild ? Slot : 'button'
  const button = <Comp className={cn(buttonVariants({ variant, className }))} ref={ref} {...props} />
  return tooltip ? <Tooltip text={tooltip}>{button}</Tooltip> : button
})
Button.displayName = 'Button'
