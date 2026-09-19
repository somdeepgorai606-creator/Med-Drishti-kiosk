import type { Metadata } from 'next';
import { Fraunces, IBM_Plex_Sans } from 'next/font/google';
import './globals.css';
import { AuthProvider } from '@/lib/auth-context';
import { LanguageProvider } from '@/lib/language-context';
import { ChatBot } from '@/components/chatbot/ChatBot';

const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-display',
});

const plexSans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
});

export const metadata: Metadata = {
  title: 'Med-Drishti — Clinical Intake System',
  description: 'AI-Powered Patient Clinical Intake Kiosk',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${fraunces.variable} ${plexSans.variable}`}>
        <AuthProvider>
          <LanguageProvider>
            {children}
            <ChatBot />
          </LanguageProvider>
        </AuthProvider>
      </body>
    </html>
  );
}

