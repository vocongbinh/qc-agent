package main

import (
	"fmt"
	"example.com/sample_go/service"
)

func main() {
	svc := &service.OrderService{}
	err := svc.ProcessOrder(&service.Order{ID: "1", Amount: 100})
	if err != nil {
		fmt.Println("Error:", err)
	}
}
