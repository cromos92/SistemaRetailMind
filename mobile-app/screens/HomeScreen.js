import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Alert } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

// Cambia localhost por tu IP local si pruebas en dispositivo físico
const API_BASE_URL = "http://localhost:8000";

export default function HomeScreen({ navigation, route }) {
  const [userName, setUserName] = useState(route.params?.userName || "");
  const [sucursalNombre, setSucursalNombre] = useState(route.params?.sucursalNombre || "");
  const [codigoAutorizacion, setCodigoAutorizacion] = useState(null);
  const [codigoInfo, setCodigoInfo] = useState(null);

  useEffect(() => {
    const cargarDatos = async () => {
      const storedUser = await AsyncStorage.getItem("user_name");
      const storedSucursal = await AsyncStorage.getItem("sucursal_nombre");
      if (storedUser) setUserName(storedUser);
      if (storedSucursal) setSucursalNombre(storedSucursal);
    };
    cargarDatos();
  }, []);

  const obtenerCodigoAutorizacion = async () => {
    try {
      const token = await AsyncStorage.getItem("token");
      if (!token) {
        Alert.alert("Error", "No hay token. Inicia sesión nuevamente.");
        return;
      }

      const response = await fetch(
        `${API_BASE_URL}/api/v1/mobile/codigo-autorizacion/actual/`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await response.json();
      if (!response.ok || !data.success) {
        Alert.alert("Error", data?.error || "No se pudo obtener el código.");
        return;
      }

      setCodigoAutorizacion(data.codigo.codigo);
      setCodigoInfo(data.codigo);
    } catch (error) {
      Alert.alert("Error", "No se pudo conectar al servidor.");
    }
  };

  const cerrarSesion = async () => {
    await AsyncStorage.multiRemove([
      "token",
      "refresh_token",
      "user_name",
      "sucursal_id",
      "sucursal_nombre",
    ]);
    navigation.replace("Login");
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Bienvenido {userName || "usuario"}</Text>
      <Text style={styles.subtitle}>Sucursal: {sucursalNombre || "Sin sucursal"}</Text>

      <TouchableOpacity style={styles.button} onPress={obtenerCodigoAutorizacion}>
        <Text style={styles.buttonText}>Ver código de autorización</Text>
      </TouchableOpacity>

      {codigoAutorizacion && (
        <View style={styles.codigoBox}>
          <Text style={styles.codigoText}>{codigoAutorizacion}</Text>
          {codigoInfo && (
            <Text style={styles.codigoInfo}>
              Válido hasta {codigoInfo.valido_hasta} · {codigoInfo.minutos_restantes} min
            </Text>
          )}
        </View>
      )}

      <TouchableOpacity
        style={[styles.button, styles.secondaryButton]}
        onPress={() => navigation.navigate("AjusteStock")}
      >
        <Text style={styles.buttonText}>Ajuste de stock</Text>
      </TouchableOpacity>

      <TouchableOpacity
        style={[styles.button, styles.logoutButton]}
        onPress={cerrarSesion}
      >
        <Text style={styles.buttonText}>Cerrar sesión</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#fff",
    padding: 24,
    justifyContent: "center",
  },
  title: {
    fontSize: 22,
    fontWeight: "700",
    textAlign: "center",
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    textAlign: "center",
    color: "#555",
    marginBottom: 20,
  },
  button: {
    backgroundColor: "#007AFF",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
    marginTop: 10,
  },
  secondaryButton: {
    backgroundColor: "#005FCC",
  },
  logoutButton: {
    backgroundColor: "#444",
  },
  buttonText: {
    color: "#fff",
    fontWeight: "600",
  },
  codigoBox: {
    marginTop: 16,
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#e6e6e6",
    alignItems: "center",
  },
  codigoText: {
    fontSize: 22,
    fontWeight: "700",
    letterSpacing: 4,
  },
  codigoInfo: {
    marginTop: 6,
    color: "#666",
  },
});
