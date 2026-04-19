import { initializeApp } from "firebase/app";
import { getAuth, createUserWithEmailAndPassword } from "firebase/auth";
import { getFirestore, doc, setDoc } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

const TEST_EMAIL = "mohak@gmail.com";
const TEST_PASSWORD = "12341234";

const userCred = await createUserWithEmailAndPassword(auth, TEST_EMAIL, TEST_PASSWORD);
const uid = userCred.user.uid;

await setDoc(doc(db, "judges", uid), {
  name: "Mohak",
  email: TEST_EMAIL,
  track: "AI/ML",
  assignedProjects: ["serverlesssync", "medscanai", "campuspulse", "neuralchef", "deepdiagnose"],
});

console.log(`Created judge: ${TEST_EMAIL} (uid: ${uid})`);
console.log("Assigned 5 test projects.");
process.exit(0);
