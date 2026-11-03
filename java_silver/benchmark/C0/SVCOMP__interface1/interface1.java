class cl {}

interface A {
  public void f();
}

class B implements A {
  public void f() {
    assert this instanceof B; // should fail
  }
}

class C implements A {
  public void f() {}
}

class Main {
  public static void main(String[] args) {
    A b = new B();
    A c = new C();
    b.f(); // really calls B.f
  }
}
