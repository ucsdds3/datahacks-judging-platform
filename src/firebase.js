import { initializeApp } from "firebase/app";
import {
  connectFirestoreEmulator,
  initializeFirestore,
  persistentLocalCache,
  persistentMultipleTabManager
} from "firebase/firestore";
import { connectAuthEmulator, getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_API_KEY,
  authDomain: import.meta.env.VITE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_APP_ID,
  measurementId: import.meta.env.VITE_MEASUREMENT_ID
};
const app = initializeApp(firebaseConfig);


// Offline persistence. Two reasons this matters at an event:
//
//   1. Resilience -- a judge on flaky venue wifi keeps working. Reads come from
//      the local cache and writes queue until the connection returns, instead
//      of failing at the moment they tap Submit.
//   2. Cost -- repeat reads are served locally rather than re-billed. A
//      refreshed leaderboard costs nothing the second time.
//
// persistentMultipleTabManager keeps the cache coherent when the same judge has
// the app open in more than one tab, which is exactly how duplicate scores got
// created before.
export const db = initializeFirestore(app, {
  localCache: persistentLocalCache({ tabManager: persistentMultipleTabManager() })
});

export const auth = getAuth(app);

// Local development against the Firebase emulators. Opt-in only:
//   VITE_USE_EMULATOR=1 npm run dev
// Never active in a normal `npm run dev` or in a production build, so this
// cannot accidentally point real judges at an empty database.
if (import.meta.env.DEV && import.meta.env.VITE_USE_EMULATOR === "1") {
  connectFirestoreEmulator(db, "127.0.0.1", 8080);
  connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
  console.info("[firebase] Using local emulators (Firestore :8080, Auth :9099)");
}