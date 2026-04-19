import { initializeApp } from "firebase/app";
import { getAuth, createUserWithEmailAndPassword } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

try {
  const cred = await createUserWithEmailAndPassword(auth, "ds3@ucsd.edu", "ds3datahacks");
  console.log(`Created: ds3@ucsd.edu  (uid: ${cred.user.uid})`);
} catch (err) {
  if (err.code === "auth/email-already-in-use") {
    console.log("ds3@ucsd.edu already exists.");
  } else throw err;
}
process.exit(0);
