import Image from 'next/image';

export const brand = {
  name: 'Reconviora',
  tagline: 'Connect. Validate. Reconcile.',
  logo: '/brand/reconviora-logo.png',
};

export function Brand() {
  if (brand.logo) return <span className="brand-logo-shell"><Image src={brand.logo} alt="Reconviora — Connect. Validate. Reconcile." width={1983} height={793} priority className="brand-logo"/></span>;
  return <span className="brand-wordmark">{brand.name}<span className="brand-caption">{brand.tagline}</span></span>;
}
