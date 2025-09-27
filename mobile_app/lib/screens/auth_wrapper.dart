import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/auth_service.dart';
import 'login_screen.dart';
import 'home_screen.dart';

class AuthWrapper extends StatelessWidget {
  const AuthWrapper({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<AuthService>(
      builder: (context, authService, child) {
        // Show loading spinner while checking authentication state
        if (authService.isLoading) {
          return const Scaffold(
            body: Center(
              child: CircularProgressIndicator(
                color: Colors.red,
              ),
            ),
          );
        }

        // Show home screen if authenticated, login screen if not
        if (authService.isAuthenticated) {
          return const HomeScreen();
        } else {
          return const LoginScreen();
        }
      },
    );
  }
}