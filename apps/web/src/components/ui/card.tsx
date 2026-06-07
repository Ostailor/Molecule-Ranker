import { clsx } from "clsx";

type CardProps = React.ComponentPropsWithoutRef<"section"> & {
  children: React.ReactNode;
};

export function Card({ children, className, ...props }: CardProps) {
  return (
    <section className={clsx("rounded-product border border-slatewash-200 bg-white shadow-line", className)} {...props}>
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  eyebrow,
  action,
  className,
}: {
  title: string;
  eyebrow?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-start justify-between gap-4 border-b border-slatewash-200 p-4", className)}>
      <div>
        {eyebrow ? <p className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">{eyebrow}</p> : null}
        <h2 className="mt-1 text-base font-semibold text-ink-950">{title}</h2>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({
  children,
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div"> & { children: React.ReactNode }) {
  return (
    <div className={clsx("p-4", className)} {...props}>
      {children}
    </div>
  );
}
