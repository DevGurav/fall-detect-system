import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';

/// Authentication service for handling user login, registration, and session management
class AuthService extends ChangeNotifier {
  static final AuthService _instance = AuthService._internal();
  factory AuthService() => _instance;
  AuthService._internal();

  final FirebaseAuth _auth = FirebaseAuth.instance;
  User? _user;
  bool _isLoading = false;

  // Getters
  User? get user => _user;
  bool get isLoading => _isLoading;
  bool get isAuthenticated => _user != null;
  String get currentUserId => _user?.uid ?? '';
  String get currentUserEmail => _user?.email ?? '';

  /// Initialize auth service and listen to auth state changes
  void initialize() {
    _auth.authStateChanges().listen((User? user) {
      _user = user;
      notifyListeners();
    });
  }

  /// Sign in with email and password
  Future<String?> signInWithEmailAndPassword(String email, String password) async {
    try {
      _isLoading = true;
      notifyListeners();

      UserCredential result = await _auth.signInWithEmailAndPassword(
        email: email.trim(),
        password: password,
      );
      
      _user = result.user;
      _isLoading = false;
      notifyListeners();
      
      return null; // Success
    } on FirebaseAuthException catch (e) {
      _isLoading = false;
      notifyListeners();
      return _getErrorMessage(e);
    } catch (e) {
      _isLoading = false;
      notifyListeners();
      return 'An unexpected error occurred';
    }
  }

  /// Register new user with email and password
  Future<String?> registerWithEmailAndPassword(String email, String password, String name) async {
    try {
      _isLoading = true;
      notifyListeners();

      UserCredential result = await _auth.createUserWithEmailAndPassword(
        email: email.trim(),
        password: password,
      );
      
      _user = result.user;
      
      // Update display name
      if (_user != null && name.isNotEmpty) {
        await _user!.updateDisplayName(name.trim());
        await _user!.reload();
        _user = _auth.currentUser;
      }
      
      _isLoading = false;
      notifyListeners();
      
      return null; // Success
    } on FirebaseAuthException catch (e) {
      _isLoading = false;
      notifyListeners();
      return _getErrorMessage(e);
    } catch (e) {
      _isLoading = false;
      notifyListeners();
      return 'An unexpected error occurred';
    }
  }

  /// Sign out current user
  Future<void> signOut() async {
    try {
      await _auth.signOut();
      _user = null;
      notifyListeners();
    } catch (e) {
      print('Error signing out: $e');
    }
  }

  /// Reset password
  Future<String?> resetPassword(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
      return null; // Success
    } on FirebaseAuthException catch (e) {
      return _getErrorMessage(e);
    } catch (e) {
      return 'An unexpected error occurred';
    }
  }

  /// Convert Firebase auth errors to user-friendly messages
  String _getErrorMessage(FirebaseAuthException e) {
    switch (e.code) {
      case 'weak-password':
        return 'The password provided is too weak.';
      case 'email-already-in-use':
        return 'The account already exists for that email.';
      case 'user-not-found':
        return 'No user found for that email.';
      case 'wrong-password':
        return 'Wrong password provided for that user.';
      case 'invalid-email':
        return 'The email address is not valid.';
      case 'user-disabled':
        return 'This user account has been disabled.';
      case 'too-many-requests':
        return 'Too many requests. Try again later.';
      case 'operation-not-allowed':
        return 'Signing in with Email and Password is not enabled.';
      default:
        return 'Authentication error: ${e.message}';
    }
  }

  /// Get current user display name or email
  String getUserDisplayName() {
    if (_user?.displayName?.isNotEmpty == true) {
      return _user!.displayName!;
    } else if (_user?.email?.isNotEmpty == true) {
      return _user!.email!;
    }
    return 'User';
  }

  /// Check if user email is verified
  bool get isEmailVerified => _user?.emailVerified ?? false;

  /// Send email verification
  Future<String?> sendEmailVerification() async {
    try {
      await _user?.sendEmailVerification();
      return null; // Success
    } catch (e) {
      return 'Failed to send verification email';
    }
  }

  /// Reload user data
  Future<void> reloadUser() async {
    try {
      await _user?.reload();
      _user = _auth.currentUser;
      notifyListeners();
    } catch (e) {
      print('Error reloading user: $e');
    }
  }
}