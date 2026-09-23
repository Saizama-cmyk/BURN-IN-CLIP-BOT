/** The keychain (iOS) / keystore (Android), for the PC address and the session token. */
import * as SecureStore from "expo-secure-store";

export const get = (key) => SecureStore.getItemAsync(key);
export const set = (key, value) => SecureStore.setItemAsync(key, value);
export const del = (key) => SecureStore.deleteItemAsync(key);
