export const THEME_KEY = "payguard-theme";

/** Runs before first paint (inlined in <head>), so a chosen theme never flashes the other one. */
export const themeBootScript = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
