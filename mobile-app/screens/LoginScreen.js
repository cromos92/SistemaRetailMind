import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  ActivityIndicator,
  Platform,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

// Cambia localhost por tu IP local si pruebas en dispositivo físico
const API_BASE_URL = "http://localhost:8000";

export default function LoginScreen({ navigation }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [codigo, setCodigo] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) {
      Alert.alert("Error", "Completa usuario y contraseña.");
      return;
    }

    setLoading(true);
    try {
      // Login JWT para app móvil (no requiere PIN 2FA)
      const response = await fetch(`${API_BASE_URL}/api/v1/desktop/login/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: username.trim(),
          password: password,
          // Si escribes un código numérico, lo usamos como sucursal_id
          sucursal_id: codigo ? Number(codigo) : null,
          device_name: "ExpoMobile",
          sistema_operativo: Platform.OS,
          version_app: "1.0.0",
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        const message =
          data?.error ||
          data?.detail ||
          data?.details ||
          "Credenciales inválidas";
        Alert.alert("Error", typeof message === "string" ? message : "Error de login");
        return;
      }

      // Guardar token y datos básicos en AsyncStorage
      await AsyncStorage.setItem("token", data.token);
      await AsyncStorage.setItem("refresh_token", data.refresh_token || "");
      await AsyncStorage.setItem("user_name", data.user_name || "");
      await AsyncStorage.setItem("sucursal_id", String(data.sucursal_id || ""));
      await AsyncStorage.setItem("sucursal_nombre", data.sucursal_nombre || "");

      Alert.alert("Éxito", "Login correcto");

      navigation.replace("Home", {
        userName: data.user_name,
        sucursalNombre: data.sucursal_nombre,
      });
    } catch (error) {
      Alert.alert("Error", "No se pudo conectar al servidor.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>Inicio de sesión</Text>

        <TextInput
          style={styles.input}
          placeholder="Usuario"
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
        />

        <TextInput
          style={styles.input}
          placeholder="Contraseña"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
        />

        <TextInput
          style={styles.input}
          placeholder="Código (Sucursal ID)"
          value={codigo}
          onChangeText={(text) => setCodigo(text.replace(/[^0-9]/g, ""))}
          keyboardType="numeric"
        />

        <TouchableOpacity style={styles.button} onPress={handleLogin} disabled={loading}>
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Ingresar</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#fff",
    justifyContent: "center",
    padding: 24,
  },
  card: {
    borderRadius: 16,
    padding: 24,
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e6e6e6",
  },
  title: {
    fontSize: 22,
    fontWeight: "600",
    marginBottom: 16,
    textAlign: "center",
  },
  input: {
    borderWidth: 1,
    borderColor: "#dcdcdc",
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginBottom: 12,
  },
  button: {
    backgroundColor: "#007AFF",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
    marginTop: 8,
  },
  buttonText: {
    color: "#fff",
    fontWeight: "600",
  },
});
