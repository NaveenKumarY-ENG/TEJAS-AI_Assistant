/** Static creator info shown in the sidebar's profile popup (Sidebar.tsx's
 *  "N" avatar). Not user-configurable at runtime — this is about the app's
 *  creator, not the assistant's end user, so it lives as a plain constant
 *  rather than in config.py/.env like ASSISTANT_NAME. */
export const PROFILE = {
  name: "NAVEEN KUMAR Y",
  email: "1kumarnaveeny@gmail.com",
  github: "https://github.com/NaveenKumarY-ENG",
  linkedin: "https://www.linkedin.com/in/naveen-kumar-y-6a52ab23a",
} as const;
