import type {Metadata,Viewport} from 'next';
import {Inter} from 'next/font/google';
import './globals.css';
import {PwaRegistration} from '@/components/pwa';
import {SplashScreen} from '@/components/splash-screen';
// Self-hosted at build time, so the font-src 'self' policy still holds.
const inter=Inter({subsets:['latin'],display:'swap',variable:'--font-sans'});
export const metadata:Metadata={title:'Reconviora | Data Workspace',description:'Reconviora — Connect. Validate. Reconcile. A connected data reconciliation and visualization workspace.',manifest:'/manifest.webmanifest',appleWebApp:{capable:true,statusBarStyle:'default',title:'Reconviora'},icons:{icon:'/icon.svg',apple:'/apple-touch-icon.png'}};
export const viewport:Viewport={themeColor:'#14213D',width:'device-width',initialScale:1};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en" className={inter.variable}><body><SplashScreen><a className="skip-link" href="#main-content">Skip to content</a>{children}</SplashScreen><PwaRegistration/></body></html>;}
