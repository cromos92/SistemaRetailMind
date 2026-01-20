import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  ActivityIndicator,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

// Cambia localhost por tu IP local si pruebas en dispositivo físico
const API_BASE_URL = "http://localhost:8000";

export default function AjusteStockScreen() {
  const [sku, setSku] = useState("");
  const [concepto, setConcepto] = useState("");
  const [cantidad, setCantidad] = useState("");
  const [observaciones, setObservaciones] = useState("");
  const [loading, setLoading] = useState(false);

  const conceptosHint =
    "AJUSTE_POSITIVO, AJUSTE_NEGATIVO, DEVOLUCION_CLIENTE, PERDIDA_ROBO...";

  const enviarAjuste = async () => {
    if (!sku || !concepto || !cantidad) {
      Alert.alert("Error", "Completa SKU, concepto y cantidad.");
      return;
    }

    setLoading(true);
    try {
      const token = await AsyncStorage.getItem("token");
      if (!token) {
        Alert.alert("Error", "No hay token. Inicia sesión nuevamente.");
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/mobile/ajuste-stock-rapido/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          sku: sku.trim(),
          concepto: concepto.trim(),
          cantidad: Number(cantidad),
          observaciones: observaciones.trim(),
        }),
      });

      const data = await response.json();
      if (!response.ok || !data.success) {
        Alert.alert("Error", data?.error || "No se pudo registrar el ajuste.");
        return;
      }

      Alert.alert("Éxito", data.message || "Ajuste registrado");
      setSku("");
      setConcepto("");
      setCantidad("");
      setObservaciones("");
    } catch (error) {
      Alert.alert("Error", "No se pudo conectar al servidor.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Ajuste rápido de stock</Text>

      <TextInput
        style={styles.input}
        placeholder="SKU"
        value={sku}
        onChangeText={(text) => setSku(text.replace(/[^0-9]/g, ""))}
        keyboardType="numeric"
      />

      <TextInput
        style={styles.input}
        placeholder="Concepto (ej: AJUSTE_POSITIVO)"
        value={concepto}
        onChangeText={setConcepto}
        autoCapitalize="characters"
      />

      <Text style={styles.hint}>{conceptosHint}</Text>

      <TextInput
        style={styles.input}
        placeholder="Cantidad"
        value={cantidad}
        onChangeText={(text) => setCantidad(text.replace(/[^0-9]/g, ""))}
        keyboardType="numeric"
      />

      <TextInput
        style={[styles.input, styles.textArea]}
        placeholder="Observaciones"
        value={observaciones}
        onChangeText={setObservaciones}
        multiline
      />

      <TouchableOpacity style={styles.button} onPress={enviarAjuste} disabled={loading}>
        {loading ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Enviar ajuste</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#fff",
    padding: 24,
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
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
  textArea: {
    minHeight: 80,
    textAlignVertical: "top",
  },
  hint: {
    color: "#666",
    fontSize: 12,
    marginBottom: 8,
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
