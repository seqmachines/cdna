import { initializeApp, getApps, cert, type App } from "firebase-admin/app";

let app: App | undefined;

export function getFirebaseApp(): App {
  if (app) return app;

  const existing = getApps();
  if (existing.length > 0) {
    app = existing[0];
    return app;
  }

  const serviceAccountJson = process.env.FIREBASE_SERVICE_ACCOUNT;
  if (serviceAccountJson) {
    app = initializeApp({ credential: cert(JSON.parse(serviceAccountJson)) });
  } else {
    // Falls back to GOOGLE_APPLICATION_CREDENTIALS file path
    app = initializeApp();
  }

  return app;
}
