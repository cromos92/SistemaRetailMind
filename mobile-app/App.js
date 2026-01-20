import React from "react";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import LoginScreen from "./screens/LoginScreen";
import HomeScreen from "./screens/HomeScreen";
import AjusteStockScreen from "./screens/AjusteStockScreen";

const Stack = createNativeStackNavigator();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator initialRouteName="Login">
        <Stack.Screen
          name="Login"
          component={LoginScreen}
          options={{ title: "Iniciar sesión" }}
        />
        <Stack.Screen
          name="Home"
          component={HomeScreen}
          options={{ title: "Inicio" }}
        />
        <Stack.Screen
          name="AjusteStock"
          component={AjusteStockScreen}
          options={{ title: "Ajuste de stock" }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
